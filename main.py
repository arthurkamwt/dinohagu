#! python

import math
import json
import requests

# consts
# start time
st = 1605834126000
# song duration + slack, in ms
dur = 150000
# multiplier from 3f to 200
burnMult = 4/3
# cp earn rate
cpRate = 20

# vars
server = 1
event = 97
interval = 60000

def loadData(server, event, interval):
    # test
    # f = open("./e97.json", "r")
    # j = json.loads(f.read())

    # prod
    j = requests.get(f'https://bestdori.com/api/eventtop/data?server={server}&event={event}&mid=0&interval={interval}').json()
    # {
    #   "points": [
    #       {"time": n, "uid": n, "value": n}, ...
    #   ],
    #   "users": [
    #       {"uid": n, "name": s, ...}, ...
    #   ]
    # }
    return j

def binPoints(points: list):
    # 3f, 200, 400, 800
    bins = ([], [], [], [])
    for p in points:
        dv = p[1]
        if dv < 7000:
            bins[0].append(dv)
        elif dv < 9000:
            bins[1].append(dv)
        elif dv < 17000:
            bins[2].append(dv)
        else:
            bins[3].append(dv)

    return bins

def calcCp(normalized):
    dt = normalized[-1][0]
    total = normalized[-1][2]

    # get bins
    bins = binPoints(normalized)
    bin0Avg = sum(bins[0]) / len(bins[0]) if len(bins[0]) else 0
    bin1Avg = sum(bins[1]) / len(bins[1]) if len(bins[1]) else 0

    # number of non-cp earning sessions
    cpCount = len(bins[1]) + len(bins[2]) + len(bins[3])
    # cp points
    cpPoints = sum(bins[1]) + sum(bins[2]) + sum(bins[3])

    # mult
    mult = bin1Avg / bin0Avg if bin0Avg > 0 and bin1Avg > 0 else burnMult

    # average binned ep
    epDt = dt - (cpCount * dur)
    epAvg = bin0Avg / dur * epDt
    epTotal = total - cpPoints
    ep = epAvg if epAvg > 0 else (epTotal if epTotal > 0 else total)

    # extrapolate based on
    # ep + cp = total
    # cp => n * ep / 20 / 200 * mult (where n = )
    # n = (total - ep) / (ep * (mult / 20 / 200)) => 4000 * (total - ep) / (ep * mult)
    n = 4000 * (total - ep) / (ep * mult)
    # todo
    # n affects epDt -> epAvg -> ep -> n

    # high, probable cp without consumption
    cpHigh = math.ceil(ep / cpRate)
    # mid, probable cp with consumption
    cpMid = math.ceil(cpHigh - (n * 200))

    # print(cpHigh, cpMid)
    return (cpHigh, cpMid)

def main():
    # todo
    # cli input

    data = loadData(server, event, interval)

    points = data["points"]
    last10 = points[-10:]
    # verify last 10 has same timestamp
    lastTs = last10[-1]["time"]
    for last in last10:
        assert last["time"] == lastTs

    # top 10 users, list of (dt from start, dv from previous, value)
    t10: dict[str, list] = { e["uid"] : [] for e in last10 }

    # aggregate top 10 user data
    for p in points:
        uid = p["uid"]
        if uid in t10.keys():
            prev: int = t10[uid][-1][2] if len(t10[uid]) > 0 else int(0)
            dt = int(p["time"]) - st
            v = int(p["value"])
            dv = int(p["value"]) - prev

            # if has change
            if dv != 0:
                t10[uid].append((dt, dv, v))

    # why even bother
    t10cp = {}
    for tk, tv in t10.items():
        t10cp[tk] = calcCp(tv)

    print(t10cp)

if __name__ == '__main__':
    main()
