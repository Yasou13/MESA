import math

def _pad(v, dim=768):
    return v + [0.0] * (dim - len(v))

VEC_BASE = _pad([1.0, 0.0])
VEC_ORTHOGONAL = _pad([0.0, 1.0])
VEC_NEAR = _pad([0.79, math.sqrt(1 - 0.79**2)])
VEC_MATCH = _pad([0.95, math.sqrt(1 - 0.95**2)])
