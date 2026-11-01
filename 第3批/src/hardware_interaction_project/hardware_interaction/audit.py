"""Read-only protected-source and isolated-copy audit."""
import ast
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SOURCE=Path('E:/eye_mouse_fpga_perf_20261016')

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    before=json.loads((ROOT/'evidence/protected_before.json').read_text(encoding='utf8'))
    changed=[r['path'] for r in before if not Path(r['path']).is_file() or sha(Path(r['path']))!=r['sha256']]
    comparison=[]
    for name in ('gimbal_uart_stage8','gimbal_fpga_stage7','interaction_fpga_stage6','stereo_fpga_stage4','software','zed2i_fpga'):
        for p in (ROOT/name).rglob('*'):
            if p.is_file() and p.suffix in ('.py','.json','.bat') and not any(x in p.parts for x in ('__pycache__','tests')):
                original=SOURCE/p.relative_to(ROOT)
                if original.is_file():
                    comparison.append({'path':p.relative_to(ROOT).as_posix(),'same_as_base':p.read_bytes()==original.read_bytes()})
    syntax=[]
    for folder in ('hardware_interaction','gimbal_uart_stage8/runtime','gimbal_uart_stage8/software','interaction_fpga_stage6/software'):
        for p in (ROOT/folder).rglob('*.py'):
            if '__pycache__' not in p.parts and 'build_source' not in p.parts:
                ast.parse(p.read_text(encoding='utf-8-sig'),filename=str(p));syntax.append(str(p.relative_to(ROOT)))
    result={'protected_files':len(before),'protected_changed':changed,
            'base_same':sum(x['same_as_base'] for x in comparison),
            'base_changed':[x['path'] for x in comparison if not x['same_as_base']],
            'syntax_checked':len(syntax),
            'old_filter_paths_absent':all(not p.exists() for p in (Path('E:/滤波一'),Path('E:/filter_one_build_20261026'),Path.home()/'Desktop/滤波一.lnk'))}
    (ROOT/'evidence/source_audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    (ROOT/'evidence/base_copy_comparison.json').write_text(json.dumps(comparison,indent=2),encoding='utf8')
    print(json.dumps(result,ensure_ascii=False))
    if changed:raise SystemExit('Protected source mismatch')
    if not result['old_filter_paths_absent']:raise SystemExit('Old filter paths remain')

if __name__=='__main__':main()
