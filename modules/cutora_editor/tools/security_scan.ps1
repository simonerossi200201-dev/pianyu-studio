# T09 security scanner.
# Buckets: 业务代码 / 测试 / 文档
# Exit 1 if 业务代码 hits > 0.

param(
    [string]$Root = ""
)

$ErrorActionPreference = "Stop"
if (-not $Root) {
    $Root = Split-Path -Parent $PSScriptRoot
}
$Root = (Resolve-Path $Root).Path

$excludeDirNames = @(
    "runtime", "_media", ".git", "venv", "venv_autosub", "__pycache__",
    "wheels", "build", ".venv", "node_modules", "_diag"
)
$textExt = @(
    ".py", ".ps1", ".bat", ".cmd", ".json", ".txt", ".md", ".in", ".toml",
    ".yml", ".yaml", ".cfg", ".ini", ".qss", ".svg", ".xml", ".html",
    ".css", ".js", ".lock", ".gitignore", ".gitattributes"
)

# Word-boundary domain / product names. `.cn` and `mirrors.` stay as substrings.
$domainPatterns = @(
    @{ Name = "domain:.cn"; Re = '\.cn\b' },
    @{ Name = "domain:aliyun"; Re = '\baliyun\b' },
    @{ Name = "domain:alibaba"; Re = '\balibaba\b' },
    @{ Name = "domain:tsinghua"; Re = '\btsinghua\b' },
    @{ Name = "domain:ustc.edu"; Re = '\bustc\.edu\b' },
    @{ Name = "domain:hf-mirror"; Re = '\bhf-mirror\b' },
    @{ Name = "domain:modelscope"; Re = '\bmodelscope\b' },
    @{ Name = "domain:baidu"; Re = '\bbaidu\b' },
    @{ Name = "domain:tencent"; Re = '\btencent\b' },
    @{ Name = "domain:qq.com"; Re = '\bqq\.com\b' },
    @{ Name = "domain:bytedance"; Re = '\bbytedance\b' },
    @{ Name = "domain:capcut"; Re = '\bcapcut\b' },
    @{ Name = "domain:kuaishou"; Re = '\bkuaishou\b' },
    @{ Name = "domain:kling"; Re = '\bkling\b' },
    @{ Name = "domain:seedance"; Re = '\bseedance\b' },
    @{ Name = "domain:douyin"; Re = '\bdouyin\b' },
    @{ Name = "domain:bilibili"; Re = '\bbilibili\b' },
    @{ Name = "domain:huawei"; Re = '\bhuawei\b' },
    @{ Name = "domain:xiaomi"; Re = '\bxiaomi\b' },
    @{ Name = "domain:163.com"; Re = '\b163\.com\b' },
    @{ Name = "domain:pypi.douban"; Re = '\bpypi\.douban\b' },
    @{ Name = "domain:mirrors."; Re = 'mirrors\.' }
)

# Qt `.exec(` is excluded: only bare eval( / exec(
$dangerPatterns = @(
    @{ Name = "danger:eval("; Re = '(?<![.\w])eval\(' },
    @{ Name = "danger:exec("; Re = '(?<![.\w])exec\(' },
    @{ Name = "danger:shell=True"; Re = 'shell\s*=\s*True' },
    @{ Name = "danger:pickle.load"; Re = 'pickle\.load' },
    @{ Name = "danger:marshal.load"; Re = 'marshal\.load' },
    @{ Name = "danger:yaml.load("; Re = 'yaml\.load\(' },
    @{ Name = "danger:os.system("; Re = 'os\.system\(' },
    @{ Name = "danger:zip-extractall"; Re = '\.extractall\(' }
)

$keyPattern = 'api_key\s*[:=]\s*["''][A-Za-z0-9_\-]{16,}'
$outboundPattern = 'urllib\.request\.urlopen\(|requests\.get\(|(?<![.\w])httpx\.'

# T04f-1d-4: third-party product names in shipped surfaces (same scope as
# tests/test_no_third_party_names.py). Dependency names (PySide6 / FFmpeg /
# faster-whisper) are not in this list.
$brandPatterns = @(
    @{ Name = "brand:davinci"; Re = '\bdavinci\b' },
    @{ Name = "brand:davinci-resolve"; Re = 'davinci[\s_-]*resolve' },
    @{ Name = "brand:capcut"; Re = '\bcapcut\b' },
    @{ Name = "brand:jianying"; Re = '剪映' },
    @{ Name = "brand:premiere"; Re = '\bpremiere\b' },
    @{ Name = "brand:final-cut"; Re = 'final\s*cut' },
    @{ Name = "brand:videokit"; Re = '\bvideokit\b' },
    @{ Name = "brand:wav2lip"; Re = '\bwav2lip\b' }
)

$whitelistHosts = @(
    "pypi.org", "files.pythonhosted.org", "download.pytorch.org",
    "python.org",
    "huggingface.co", "github.com", "objects.githubusercontent.com",
    "raw.githubusercontent.com", "fonts.google.com",
    "api.openai.com", "api.anthropic.com",
    "generativelanguage.googleapis.com", "api.x.ai",
    "127.0.0.1", "localhost"
)

function Test-ExcludedDir([string]$full) {
    $rel = $full.Substring($Root.Length).TrimStart('\', '/')
    $parts = $rel -split '[\\/]'
    foreach ($p in $parts) {
        if ($excludeDirNames -contains $p) { return $true }
        if ($p -like '*-venv') { return $true }
    }
    if ($rel -match '(^|[\\/])tests[\\/]golden[\\/]_media([\\/]|$)') { return $true }
    if ($rel -match '(^|[\\/])runtime([\\/]|$)') { return $true }
    if ($rel -match '(^|[\\/])resources[\\/]fonts[\\/]') { return $true }
    # Task papers / review stills. Not product source; the docs bucket grew
    # past two minutes because every Txx report was re-read on each scan.
    if ($rel -match '(^|[\\/])docs[\\/]plan([\\/]|$)') { return $true }
    return $false
}

function Get-Bucket([string]$rel) {
    $n = $rel -replace '/', '\'
    if ($n -match '(^|\\)docs\\' -or $n -match '\.md$') { return "docs" }
    if ($n -match '(^|\\)tests\\') { return "test" }
    if ($n -match '(^|\\)vendor\\') { return "docs" }
    if ($n -match '(^|\\)resources[\\/]data\\') { return "docs" }
    if ($n -match '(^|\\)build\\') { return "docs" }
    if ($n -match '(^|\\)marketing\\' -or $n -match '(^|\\)packaging\\') { return "docs" }
    return "code"
}

function Test-SkipDomain([string]$rel) {
    $n = $rel -replace '/', '\'
    if ($n -match '(^|\\)docs\\') { return $true }
    if ($n -match 'tools\\security_scan\.ps1$') { return $true }
    if ($n -match '(^|\\)resources[\\/]data\\') { return $true }
    if ($n -match '\.md$') { return $true }
    return $false
}

function Test-SkipDanger([string]$rel) {
    $n = $rel -replace '/', '\'
    if ($n -match '(^|\\)docs\\') { return $true }
    if ($n -match 'tools\\security_scan\.ps1$') { return $true }
    if ($n -match '\.md$') { return $true }
    return $false
}

function Test-SkipBrand([string]$rel) {
    $n = $rel -replace '/', '\'
    if ($n -match 'tools\\security_scan\.ps1$') { return $true }
    if ($n -match 'tests\\test_no_third_party_names\.py$') { return $true }
    if ($n -match 'cutora\\core\\migrate_user_data\.py$') { return $true }
    if ($n -match '(^|\\)resources\\fonts\\') { return $true }
    if ($n -match '(^|\\)resources\\fonts_pending\\') { return $true }
    if ($n -match '(^|\\)resources\\data\\linebreak_.*\.json$') { return $true }
    if ($n -match '(^|\\)resources\\data\\stopwords_.*\.json$') { return $true }
    if ($n -match '(^|\\)resources\\data\\wenan_words\.txt$') { return $true }
    $inScope = (
        $n -match '(^|\\)cutora\\' -or
        $n -match '(^|\\)resources\\' -or
        $n -match '(^|\\)README' -or
        $n -match '(^|\\)NOTICE'
    )
    if (-not $inScope) { return $true }
    return $false
}

$files = Get-ChildItem -LiteralPath $Root -Recurse -File -Force -ErrorAction SilentlyContinue |
    Where-Object {
        -not (Test-ExcludedDir $_.DirectoryName) -and
        ($textExt -contains $_.Extension.ToLower() -or $_.Name -in @(".gitignore", ".gitattributes", "requirements.lock", "requirements.in"))
    }

$hits = New-Object System.Collections.Generic.List[object]

function Add-Hit([string]$rel, [int]$n, [string]$kind, [string]$line, [string]$bucket) {
    $hits.Add([pscustomobject]@{
        Rel = $rel; Line = $n; Kind = $kind; Text = $line.Trim(); Bucket = $bucket
    })
}

foreach ($f in $files) {
    $rel = $f.FullName.Substring($Root.Length).TrimStart('\', '/')
    $bucket = Get-Bucket $rel
    try {
        $lines = Get-Content -LiteralPath $f.FullName -Encoding UTF8 -ErrorAction Stop
    } catch {
        continue
    }
    $n = 0
    $skipDomain = Test-SkipDomain $rel
    $skipDanger = Test-SkipDanger $rel
    $skipBrand = Test-SkipBrand $rel
    $npath = ($rel -replace '/', '\')
    $skipOutbound = (
        $npath -match '(^|\\)cutora\\core\\net\.py$' -or
        $npath -match 'tools\\fetch_t09_fonts\.py$'
    )
    foreach ($line in $lines) {
        $n++
        if (-not $skipDomain) {
            foreach ($pat in $domainPatterns) {
                if ($line -imatch $pat.Re) {
                    Add-Hit $rel $n $pat.Name $line $bucket
                }
            }
        }
        if (-not $skipDanger) {
            foreach ($pat in $dangerPatterns) {
                if ($pat.Name -eq "danger:shell=True" -and $npath -match 'cutora\\core\\proc\.py$') {
                    continue
                }
                if ($line -imatch $pat.Re) {
                    Add-Hit $rel $n $pat.Name $line $bucket
                }
            }
        }
        if (-not $skipBrand) {
            foreach ($pat in $brandPatterns) {
                if ($line -imatch $pat.Re) {
                    Add-Hit $rel $n $pat.Name $line $bucket
                }
            }
        }
        if ($line -imatch $keyPattern) {
            Add-Hit $rel $n "secret:api_key" $line $bucket
        }
        if (-not $skipOutbound -and ($line -imatch $outboundPattern)) {
            Add-Hit $rel $n "outbound:direct" $line $bucket
        }
        if ($bucket -eq "code" -and -not ($npath -match 'tools\\security_scan\.ps1$')) {
            if ($line -imatch '0\.0\.0\.0') {
                Add-Hit $rel $n "bind:all-interfaces" $line $bucket
            }
            if ($line -imatch 'bind\(\s*\(\s*""') {
                Add-Hit $rel $n "bind:empty-host" $line $bucket
            }
            $isNetPy = $npath -match 'cutora\\core\\net\.py$'
            $inHfRedirect = $isNetPy -and ($line -imatch 'redirect')
            if ($line -imatch '\bhf\.co\b' -and -not $inHfRedirect) {
                Add-Hit $rel $n "policy:hf.co-direct" $line $bucket
            }
        }
        if ($f.Extension -in @(".bat", ".cmd")) {
            $urlMatches = [regex]::Matches($line, 'https?://([^/\s:"'']+)')
            foreach ($m in $urlMatches) {
                $hostName = $m.Groups[1].Value.ToLower()
                $ok = $false
                foreach ($w in $whitelistHosts) {
                    if ($hostName -eq $w -or $hostName.EndsWith("." + $w)) { $ok = $true; break }
                }
                if (-not $ok) {
                    Add-Hit $rel $n ("bat:url:" + $hostName) $line $bucket
                }
            }
        }
    }
}

$codeHits = @($hits | Where-Object { $_.Bucket -eq "code" })
$testHits = @($hits | Where-Object { $_.Bucket -eq "test" })
$docsHits = @($hits | Where-Object { $_.Bucket -eq "docs" })

Write-Output ("ROOT=" + $Root)
Write-Output ("FILES_SCANNED=" + $files.Count)
Write-Output ("HITS=" + $hits.Count)
Write-Output ("HITS_CODE=" + $codeHits.Count)
Write-Output ("HITS_TEST=" + $testHits.Count)
Write-Output ("HITS_DOCS=" + $docsHits.Count)
Write-Output "----- 业务代码 -----"
foreach ($h in $codeHits) {
    Write-Output ("{0}:{1}: [{2}] {3}" -f $h.Rel, $h.Line, $h.Kind, $h.Text)
}
Write-Output "----- 测试 -----"
foreach ($h in $testHits) {
    Write-Output ("{0}:{1}: [{2}] {3}" -f $h.Rel, $h.Line, $h.Kind, $h.Text)
}
Write-Output "----- 文档 -----"
foreach ($h in $docsHits) {
    Write-Output ("{0}:{1}: [{2}] {3}" -f $h.Rel, $h.Line, $h.Kind, $h.Text)
}
Write-Output "----- END -----"

if ($codeHits.Count -gt 0) {
    exit 1
}
exit 0
