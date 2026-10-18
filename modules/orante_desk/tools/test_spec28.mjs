// 规格 28：TSV 解析 / 填充柄 / 粘贴落位 的纯函数验收。不启动 Orante。
import { createRequire } from 'module';
import { fileURLToPath } from 'url';
import path from 'path';

const require = createRequire(import.meta.url);
const here = path.dirname(fileURLToPath(import.meta.url));
const Sheet = require(path.join(here, '..', 'ui', 'js', 'parts', 'sheet.js'));

let failed = 0;
function eq(name, got, want) {
  const a = JSON.stringify(got);
  const b = JSON.stringify(want);
  if (a === b) {
    console.log('  ok  ' + name);
    return;
  }
  failed += 1;
  console.log('  FAIL ' + name);
  console.log('    got ', a);
  console.log('    want', b);
}

function assert(name, cond, detail) {
  if (cond) console.log('  ok  ' + name);
  else {
    failed += 1;
    console.log('  FAIL ' + name + (detail ? '  ' + detail : ''));
  }
}

console.log('1. TSV 带换行 / 引号 / Tab');
{
  const long = '第一行\n第二行里有个""引号""\n第三行';
  // 手写一段 Google Sheet 风格的 TSV：文案列带换行和引号
  const grid = [
    ['任务A', long, 'http://img', '短文案', 'Lucy', '6', 'x', 'y'],
    ['任务B', '短句', 'folder', '长文案', 'TIK-G-SIMONE', '8', 'p', 'q'],
  ];
  const tsv = Sheet.serializeTSV(grid);
  assert('serialize quotes the copy cell', tsv.indexOf('"第一行') === 0 || tsv.indexOf('\t"第一行') > 0 || /"第一行/.test(tsv), tsv.slice(0, 80));
  assert('serialize has literal quotes as ""', tsv.includes('""引号""'), tsv);
  const back = Sheet.parseTSVGrid(tsv);
  eq('roundtrip 2x8', back, grid);
  assert('long copy stays 1 cell', back[0][1] === long, JSON.stringify(back[0][1]));
  assert('not split into extra rows', back.length === 2, 'rows=' + back.length);
}

{
  // 模拟 Sheet 复制：CRLF 行分隔、文案里是 LF
  const copy = 'Hello\n"quoted"\nworld';
  const tsv = [
    'n1\t"' + copy.replace(/"/g, '""') + '"\timg\tshort',
    'n2\tplain\timg2\tlong',
  ].join('\r\n') + '\r\n';
  const g = Sheet.parseTSVGrid(tsv);
  eq('crlf rows', g.length, 2);
  eq('newline inside quotes is content', g[0][1], copy);
  eq('inner quotes restored', g[0][1].includes('"quoted"'), true);
}

{
  const g = Sheet.parseTSVGrid('a\tb\n"c\td"\te');
  eq('tab inside quotes', g, [['a', 'b'], ['c\td', 'e']]);
}

{
  const header = Sheet.parseTSVGrid('任务名\t文案\t图片\nA\tB\tC');
  const stripped = Sheet.stripHeaderRow(header);
  eq('strip header', stripped, [['A', 'B', 'C']]);
}

console.log('2. 200 行 × 8 列（文案带换行）整表往返');
{
  const rows = [];
  for (let i = 1; i <= 200; i++) {
    const copy = i % 7 === 0
      ? `长文案第${i}条\n第二段 "引号" 和\ttab\n结尾`
      : `短文案 ${i}`;
    rows.push([
      'T' + String(i).padStart(4, '0'),
      copy,
      'https://drive.google.com/x/' + i,
      i % 2 ? '短文案' : '长文案',
      i % 3 ? 'TIK-G-SIMONE' : 'Lucy',
      String([6, 8, 10][i % 3]),
      'note' + i,
      i % 5 ? 'ok' : '',
    ]);
  }
  const tsv = Sheet.serializeTSV(rows);
  const back = Sheet.parseTSVGrid(tsv);
  eq('200 rows', back.length, 200);
  eq('8 cols', back[0].length, 8);
  let split = 0;
  for (let i = 0; i < 200; i++) {
    if (back[i][1] !== rows[i][1]) split += 1;
  }
  eq('no copy cell split', split, 0);
  eq('row 7 copy has newline', back[6][1].includes('\n'), true);
  eq('full grid equal', back, rows);
}

console.log('3. 填充柄');
{
  const v = Sheet.fillValues(['李良v01'], 20, 1, 'auto', false);
  eq('李良v01 down 20 last', v[v.length - 1], '李良v21');
  eq('李良v01 down first', v[0], '李良v02');
  eq('20 values', v.length, 20);
  const copy = Sheet.fillValues(['李良v01'], 20, 1, 'copy', false);
  eq('ctrl copy all same', copy.every(x => x === '李良v01'), true);
  const arith = Sheet.fillValues(['2', '4'], 5, 1, 'auto', false);
  eq('2,4 arithmetic', arith, ['6', '8', '10', '12', '14']);
  const pad = Sheet.fillValues(['01'], 10, 1, 'auto', false);
  eq('leading zero 01→10', pad[8], '10'); // 02..10 is index 8 = 10
  eq('leading zero 02', pad[0], '02');
  const list = Sheet.fillValues(['long'], 4, 1, 'auto', true);
  eq('list col copies', list, ['long', 'long', 'long', 'long']);
  const up = Sheet.fillValues(['李良v05'], 3, -1, 'auto', false);
  eq('up decrement', up, ['李良v04', '李良v03', '李良v02']);
}

console.log('4. 粘贴落位');
{
  eq('single cell expands', Sheet.pastePlan(1, 1, 200, 8, true), {
    destRows: 200, destCols: 8, tile: false, addRows: true,
  });
  eq('exact overlay', Sheet.pastePlan(3, 2, 3, 2, false), {
    destRows: 3, destCols: 2, tile: true, addRows: false,
  });
  eq('integer multiple tiles', Sheet.pastePlan(6, 4, 3, 2, false), {
    destRows: 6, destCols: 4, tile: true, addRows: false,
  });
  const g = [['a', 'b'], ['c', 'd']];
  eq('tile value (0,0)', Sheet.pasteValue(g, 0, 0), 'a');
  eq('tile value (2,2)', Sheet.pasteValue(g, 2, 2), 'a');
  eq('tile value (3,1)', Sheet.pasteValue(g, 3, 1), 'd');
}

console.log('5. 剪贴板 HTML 超链接（规格 45）');
{
  const drive = 'https://drive.google.com/drive/folders/abc';
  const wrapped = 'https://www.google.com/url?q=' + encodeURIComponent(drive) + '&sa=D&source=editors&ust=1';
  eq('unwrap google /url', Sheet.unwrapGoogleUrl(wrapped), drive);
  eq('unwrap keeps plain', Sheet.unwrapGoogleUrl(drive), drive);

  const html2026 = `<google-sheets-html-origin><table data-sheets-root="1"><tbody>
    <tr><td>任务名</td><td>文案</td><td>图片来源</td></tr>
    <tr><td>李星辰v01</td><td>hello</td>
      <td style="text-decoration:underline"><a class="in-cell-link" href="${drive}" target="_blank">李良素材</a></td>
    </tr>
  </tbody></table></google-sheets-html-origin>`;
  const g2026 = Sheet.parseHtmlGridLinks(html2026);
  eq('2026 a.in-cell-link rows', g2026 && g2026.length, 2);
  eq('2026 a.in-cell-link href', g2026 && g2026[1][2] && g2026[1][2].href, drive);
  eq('2026 a.in-cell-link text', g2026 && g2026[1][2] && g2026[1][2].text, '李良素材');

  const htmlAttr = `<table><tr><td data-sheets-hyperlink="${drive}">李良素材</td></tr></table>`;
  const gAttr = Sheet.parseHtmlGridLinks(htmlAttr);
  eq('data-sheets-hyperlink', gAttr && gAttr[0][0] && gAttr[0][0].href, drive);

  const htmlFormula = `<table><tr><td data-sheets-formula="=HYPERLINK(&quot;${drive}&quot;,&quot;李良素材&quot;)">李良素材</td></tr></table>`;
  const gF = Sheet.parseHtmlGridLinks(htmlFormula);
  eq('data-sheets-formula HYPERLINK', gF && gF[0][0] && gF[0][0].href, drive);

  const htmlWrap = `<table><tr><td><a href="${wrapped}">李良素材</a></td></tr></table>`;
  const gW = Sheet.parseHtmlGridLinks(htmlWrap);
  eq('unwrap inside href', gW && gW[0][0] && gW[0][0].href, drive);

  const htmlMerge = `<table>
    <tr><td colspan="2">A</td><td>C</td></tr>
    <tr><td>d</td><td>e</td><td>f</td></tr>
  </table>`;
  const gM = Sheet.parseHtmlGridLinks(htmlMerge);
  eq('colspan expands to 3 cols', gM && gM[0].length, 3);
  eq('colspan copies value', gM && gM[0][1] && gM[0][1].text, 'A');
  eq('rowspan not needed still 2 rows', gM && gM.length, 2);

  const htmlRs = `<table>
    <tr><td rowspan="2">A</td><td>B</td></tr>
    <tr><td>D</td></tr>
  </table>`;
  const gR = Sheet.parseHtmlGridLinks(htmlRs);
  eq('rowspan expands 2x2', gR && gR.length === 2 && gR[0].length === 2, true);
  eq('rowspan fill', gR && gR[1][0] && gR[1][0].text, 'A');
  eq('rowspan sibling', gR && gR[1][1] && gR[1][1].text, 'D');

  const text = [['李星辰v01', 'hello', '李良素材']];
  eq('linksAlign same shape', Sheet.linksAlign(text, [[{text:'x',href:null},{text:'y',href:null},{text:'z',href:drive}]]), true);
  eq('linksAlign reject shorter html', Sheet.linksAlign(text, [[{text:'x',href:null}]]), false);
  eq('firstHref', Sheet.firstHrefFromHtml(html2026), drive);
}

if (failed) {
  console.log('\nFAILED ' + failed);
  process.exit(1);
}
console.log('\nall passed');
