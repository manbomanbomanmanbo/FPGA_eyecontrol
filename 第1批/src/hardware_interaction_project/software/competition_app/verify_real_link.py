"""Explicit hardware acceptance test; synthetic vectors, never gaze accuracy."""
import argparse
import json
from pathlib import Path
import random
import time
import statistics
from mock_link.calibration import build_profile, sha256_id, q16_from_float
from mock_link.host_client import HostClient
from .calibration import integer_reference


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--host', default='192.168.2.99')
    p.add_argument('--port', type=int, default=8765)
    p.add_argument('--output', required=True)
    args = p.parse_args()
    output = Path(args.output); output.mkdir(parents=True, exist_ok=True)
    rng = random.Random(20260930)
    cases = [
        (1920,1080,[1919,0,0,0],[0,1079,0,0]),
        (1280,720,[900,-100,250,45],[-80,650,-170,110]),
        (800,600,[-850,130,-225,760],[130,-500,100,510]),
        (2560,1440,[2600,-80,350,-90],[-70,1450,-400,85]),
    ]
    latencies=[]; cpu_times=[]; profiles=[]; invalid=0; total=0
    for idx,(width,height,a,b) in enumerate(cases):
        wire=build_profile(sha256_id({'fixture':'hardware_acceptance','case':idx}),width,height,
                           [q16_from_float(v) for v in a],[q16_from_float(v) for v in b])
        points=[(x,y) for x in (-16384,-1,0,32768,65536,81920)
                      for y in (-16384,-1,0,32768,65536,81920)]
        points += [(rng.randint(-16384,81920),rng.randint(-16384,81920)) for _ in range(128)]
        with HostClient(host=args.host,port=args.port,calibration=wire,timeout_s=1,
                        log_path=str(output/('client_%d.jsonl'%idx))) as client:
            for gx,gy in points:
                t=time.perf_counter_ns(); expected=integer_reference(gx,gy,wire)
                cpu_times.append((time.perf_counter_ns()-t)/1e6)
                t=time.perf_counter_ns(); result=client.submit(gx,gy)
                latencies.append((time.perf_counter_ns()-t)/1e6)
                if result.status!='ok' or (result.screen_x,result.screen_y)!=expected:
                    raise AssertionError({'case':idx,'input':[gx,gy],'expected':expected,'actual':result.__dict__})
                total+=1
            result=client.submit(0,0,valid=0)
            if result.screen_x is not None or result.screen_y is not None or client.last_result is not None:
                raise AssertionError('invalid frame retained coordinates')
            invalid+=1
            profiles.append({'calibration_id':wire['calibration_id'],'session_id':client.session_id,
                             'width':width,'height':height,'valid_vectors':len(points)})
    def summary(values):
        ordered=sorted(values)
        return {'median_ms':statistics.median(values),'p95_ms':ordered[int(.95*(len(ordered)-1))],
                'max_ms':max(values),'samples':len(values)}
    result={'status':'PASS','evidence':'real_FPGA_with_synthetic_vectors','valid_vectors':total,
            'invalid_frames':invalid,'profiles':profiles,'mismatches':0,
            'pc_network_roundtrip':summary(latencies),'pc_integer_reference_with_validation':summary(cpu_times),
            'benchmark_limit':'Different scopes: network/serialization/PS/MMIO versus local Python reference. No FPGA speedup claim.',
            'personal_gaze_accuracy_measured':False,'os_mouse_used':False}
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
