"""Build the packaged RTL in a new ASCII-only output directory."""
import argparse
from pathlib import Path
import subprocess
import sys

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--destination',type=Path,required=True)
    p.add_argument('--vivado',type=Path,required=True)
    a=p.parse_args()
    root=Path(__file__).resolve().parents[1]
    return subprocess.call([sys.executable,'-B',str(root/'src/hardware_interaction_project/hardware_interaction/rebuild.py'),
                            '--destination',str(a.destination),'--vivado',str(a.vivado)])
if __name__=='__main__':sys.exit(main())
