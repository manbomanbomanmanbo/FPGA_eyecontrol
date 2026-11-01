"""Recreate the local Vivado build in a NEW ASCII-only output directory."""
import argparse
from pathlib import Path
import shutil
import subprocess

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination',type=Path,required=True)
    parser.add_argument('--vivado',type=Path,default=Path('E:/vivado/Vivado/2024.2/bin/vivado.bat'))
    args=parser.parse_args()
    target=args.destination.resolve()
    if not str(target).isascii():parser.error('Use an ASCII-only build directory')
    if target.exists():parser.error('Destination must not exist; original builds are never overwritten')
    root=Path(__file__).resolve().parent
    target.mkdir(parents=True)
    shutil.copytree(root/'build_source',target/'baseline')
    (target/'baseline/reports').mkdir()
    shutil.copy2(root/'build_source/rebuilt_bd.tcl',target/'baseline/reports/rebuilt_bd.tcl')
    shutil.copy2(root/'build_hardware_interaction.tcl',target/'build_hardware_interaction.tcl')
    subprocess.run([str(args.vivado),'-mode','batch','-source','build_hardware_interaction.tcl',
                    '-log','build.log','-journal','build.jou'],cwd=target,check=True)

if __name__=='__main__':main()
