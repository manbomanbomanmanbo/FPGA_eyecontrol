"""Hardware-only observation client. No software threshold/state fallback."""
import time
from interaction_stage6.runtime_adapter import InteractionAdapter, InteractionFailure, q16

class HardwareInteractionAdapter(InteractionAdapter):
    def __init__(self, client, trace=None, clock=time.monotonic):
        super().__init__(client, trace)
        self.operation_counts.update({10:0,11:0,12:0})
        self._clock=clock
        self._epoch=clock()
        self._sequences={10:0,11:0}
        self.eye_result=None
        self.mouth_result=None

    def observation_ms(self):
        return int((self._clock()-self._epoch)*1000) & 0xffffffff

    def _sequence(self, op):
        self._sequences[op]+=1
        if self._sequences[op]>0xffffffff:
            raise InteractionFailure('Observation sequence exhausted; restart')
        return self._sequences[op]

    def observe_eyes(self, left, right, generation, *, valid=True, stamp=None):
        seq=self._sequence(10)
        ms=self.observation_ms() if stamp is None else stamp
        out=self._call(10,q16(left),q16(right),ms,seq,int(generation)&0xffffffff,int(valid))
        if out[0] not in (0,1,2) or out[1] not in (0,1) or out[4] not in (0,1) or out[6:] != [seq,ms]:
            self.failed=True
            raise InteractionFailure('Invalid PL eyelid result')
        self.eye_result={'class':out[0],'rearm_required':bool(out[1]),'reopen_frames':out[2],
                         'reopen_elapsed_ms':out[3],'update_baseline':bool(out[4]),
                         'flags':out[5],'seq':seq,'observation_ms':ms}
        return self.eye_result

    def observe_mouth(self, ratio, generation, *, valid=True, stamp=None):
        seq=self._sequence(11)
        ms=self.observation_ms() if stamp is None else stamp
        out=self._call(11,q16(ratio),ms,seq,int(generation)&0xffffffff,int(valid))
        if out[0] not in (0,1) or out[6:] != [seq,ms]:
            self.failed=True
            raise InteractionFailure('Invalid PL mouth result')
        self.mouth_result={'open':bool(out[0]),'threshold_q16':out[1],
                           'flags':out[5],'seq':seq,'observation_ms':ms}
        return self.mouth_result

    def reset_observations(self, mask=3):
        self._call(12,mask)
