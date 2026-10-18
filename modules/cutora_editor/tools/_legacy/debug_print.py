import codecs

lines = codecs.open('AutoSub_debug.py', 'r', 'utf-8').read()

hook = """                    if _font_changed:
                        word_font = get_font_manager().get_font("""
                        
rep = """                    if auto_color_rule and "signore" in word_clean:
                        print(f"DEBUG SIGNORE: bold={_cur_bold}, font_f={_cur_family}, font_s={_cur_size}, font_change={_font_changed}")
                    if _font_changed:
                        word_font = get_font_manager().get_font("""
                        
lines = lines.replace(hook, rep)

hook2 = """                    _eff_bw = border_w_metro
                    if word_use_stroke and _eff_bw > 0:"""

rep2 = """                    _eff_bw = border_w_metro
                    if auto_color_rule and "signore" in word_clean:
                        print(f"DEBUG STROKE: stroke_w={_eff_bw}, use_stroke={word_use_stroke}")
                    if word_use_stroke and _eff_bw > 0:"""

lines = lines.replace(hook2, rep2)

with codecs.open('AutoSub_debug.py', 'w', 'utf-8') as f:
    f.write(lines)
