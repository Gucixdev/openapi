# Which free providers to sign up for next, so a failover has the most INDEPENDENT places to land.
# Honest scope: the structure (kind, key already held) comes from providers.json; the three weights below are a policy I chose, not a measurement.
set CAND;                          # free providers we hold no key for, endpoint not dead
param kind {CAND} symbolic;        # direct | gateway | regional
param value {CAND};                # independence weight from kind
param K integer >= 1;             # how many signups we are willing to do
param maxgateway integer >= 0;    # gateways re-sell other providers' models: correlated with providers we already have
param maxregional integer >= 0;   # regional signups often need a local phone/ID

var pick {CAND} binary;
maximize independence: sum {c in CAND} value[c] * pick[c];
subject to budget: sum {c in CAND} pick[c] <= K;
subject to gateways: sum {c in CAND: kind[c] = 'gateway'} pick[c] <= maxgateway;
subject to regionals: sum {c in CAND: kind[c] = 'regional'} pick[c] <= maxregional;
