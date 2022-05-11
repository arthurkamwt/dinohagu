#! python3.9

import math
import json
import requests

# consts
# song duration + slack, in ms
duration = 150000
# multiplier from 3f to 200
defaultMult = 4/3
# cp earn rate
cpepRate = 20

# vars
server = 1
event = 153
interval = 60000
# startTime = 1605834126000 # e97
startTime = 1652230800000 # e153


###########################
# Classes / class helpers #
###########################

def binPoints(points: list[tuple[int, int]]) -> tuple[list[int], list[int], list[int], list[int]]:
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

class UserData:
    def __init__(self, uid: str, rawTsd: list[tuple[int, int]]):
        self.uid = uid
        # self.rawTsd = []
        # self.normTsd = []
        # self.zeroes = 0
        # self.bins = ([], [], [], [])
        self.setTsd(rawTsd)
    
    # raw time series data: timestamp, value
    def setTsd(self, rawTsd: list[tuple[int, int]]):
        self.rawTsd = rawTsd

        self.setNormalizeTsd()
        self.setBinTsd()

    # normtsd: dt, dv, maybe 2nd deriv?
    def setNormalizeTsd(self):
        normTsd: list[tuple[int, int]] = []
        prevT = 0
        prevV = 0
        zeroes = 0
        for raw in self.rawTsd:
            nt = raw[0] - startTime
            dt = nt - prevT
            dv = raw[1] - prevV
            if dt >= interval * 1.2: # uncertainty
                # theres a skip
                dv = math.ceil(dv * duration / dt)

            # if dv > 0:
            #     print("points/ms\t", math.ceil(dv * 1000 / dt), "\tdv\t", dv, "\tdt\t", dt)

            prevT = nt
            prevV = raw[1]

            if dv != 0:
                normTsd.append((nt, dv))
            else:
                zeroes += 1

        self.normTsd = normTsd
        self.zeroes = zeroes

    def setBinTsd(self):
        self.bins = binPoints(self.normTsd)

    #########
    # Utils #
    #########

    def getTotal(self) -> int:
        if len(self.rawTsd) > 0:
            return self.rawTsd[-1][1]
        else:
            return 0

    def getDt(self) -> int:
        if len(self.normTsd) > 0:
            return self.normTsd[-1][0]
        else:
            return 0

    def getBinSums(self) -> tuple[int, int, int, int]:
        return (
            sum(self.bins[0]),
            sum(self.bins[1]),
            sum(self.bins[2]),
            sum(self.bins[3])
        )

    def getBinCounts(self) -> tuple[int, int, int, int]:
        return (
            len(self.bins[0]),
            len(self.bins[1]),
            len(self.bins[2]),
            len(self.bins[3])
        )

    def getZeroes(self) -> int:
        return self.zeroes


    #######
    # *-* #
    #######

    def calculateCp(self):
        total = self.getTotal()
        dt = self.getDt()
        sums = self.getBinSums()
        counts = self.getBinCounts()
        zeroes = self.getZeroes()

        # avgs
        avgs = (
            math.ceil(sums[0] / counts[0]) if counts[0] > 0 else int(0),
            math.ceil(sums[1] / counts[1]) if counts[1] > 0 else int(0),
            math.ceil(sums[2] / counts[2]) if counts[2] > 0 else int(0),
            math.ceil(sums[3] / counts[3]) if counts[3] > 0 else int(0)
        )

        # historical cp
        # epp / 20 - n200 * 200 - n400 * 400 - n800 * 800
        historicalCp = math.ceil(sums[0] / cpepRate) - (counts[1] * 200) - (counts[2] * 400) - (counts[3] * 800)


        # extrapolation
        exTotal = total - sum(sums)
        # duration is kind of trash, maybe use zeroes to estimate duration too
        # number of runs
        exCount = (dt / duration) - sum(counts)
        exEv = math.ceil(exTotal / exCount)

        # print("common")
        # print("\ttotal\t", total)
        # print("\tdt\t", dt)
        # print("\tavgs\t", avgs)
        # print("\tsums\t", sums)
        # print("\tcounts\t", counts)

        # print("hist")
        # print("\thistoricalCp\t", historicalCp)

        # print("exs")
        # print("\texTotal\t", exTotal)
        # print("\texCount\t", exCount)
        # print("\texEv\t", exEv)
        # print()
        # print("\tdt\t", dt)
        # print("\tcount * dur\t", sum(counts) * duration)
        # print("\tdt / dur\t", math.ceil(dt / duration))
        # print("\tsum counts\t", sum(counts))

        # cp/ep multiplier
        cpepMult = avgs[1] / avgs[0] if avgs[0] > 0 else defaultMult

        if (counts[0] > 0):
            ev3f = avgs[0]
        elif (counts[1] > 0):
            ev3f = math.ceil(avgs[1] / cpepMult)
        elif (counts[2] > 0):
            ev3f = math.ceil(avgs[2] / (cpepMult * 2))
        else:
            ev3f = math.ceil(avgs[3] / (cpepMult * 4))
        ev200 = avgs[1] if counts[1] > 0 else math.ceil(ev3f * cpepMult)
        ev400 = avgs[2] if counts[2] > 0 else ev200 * 2
        ev800 = avgs[3] if counts[3] > 0 else ev400 * 2

        # percentage of cp plays
        p200 = max(round((exEv - ev3f) / (ev200 - ev3f), 4), 0)
        p400 = max(round((exEv - ev3f) / (ev400 - ev3f), 4), 0)
        p800 = max(round((exEv - ev3f) / (ev800 - ev3f), 4), 0)

        print("avgs\t", avgs)
        print("exEv\t", exEv)
        print("ex%cp\t", (p200, p400, p800))

        # cp = count * ((1 - px) * ev3f / 20 - px * x), x E { 200, 400, 800 }
        ev3f20 = math.ceil(ev3f / 20)

        cp200 = math.ceil(exCount * ((1 - p200) * ev3f20 - (p200 * 200))) + historicalCp
        cp400 = math.ceil(exCount * ((1 - p400) * ev3f20 - (p400 * 400))) + historicalCp
        cp800 = math.ceil(exCount * ((1 - p800) * ev3f20 - (p800 * 800))) + historicalCp

        return (cp200, cp400, cp800)


###########
# Helpers #
###########

def loadData(server: int, event: int, interval: int, isFile: bool):
    fn = f'./e{event}.json'

    if isFile:
        # test
        f = open(fn, "r")
        j = json.loads(f.read())
    else:
        # prod
        url = f'https://bestdori.com/api/eventtop/data?server={server}&event={event}&mid=0&interval={interval}'
        print(url)
        j = requests.get(url).json()
        f = open(fn, "w")
        f.write(json.dumps(j))
    # {
    #   "points": [
    #       {"time": n, "uid": n, "value": n}, ...
    #   ],
    #   "users": [
    #       {"uid": n, "name": s, ...}, ...
    #   ]
    # }
    return j

def main():
    # todo
    # cli input

    data = loadData(server, event, interval, False)

    points = data["points"]

    last10 = points[-10:]
    # verify last 10 has same timestamp
    lastTs = last10[-1]["time"]
    for last in last10:
        assert last["time"] == lastTs

    # top 10 users, list of (dt from start, dv from previous, value)
    # t10: dict[int, list] = { 3645020: [] }
    t10: dict[str, list] = { e["uid"] : [] for e in last10 }

    # aggregate top 10 user data
    for p in points:
        uid = p["uid"]
        if uid in t10.keys():
            t = int(p["time"])
            v = int(p["value"])
            t10[uid].append((t, v))
    
    userData = { k : UserData(uid, v) for k, v in t10.items() }

    users = data["users"]
    userNames = { u["uid"] : u["name"] for u in users }

    for k, v in userData.items():
        print("user\t", k, userNames[k])
        print("cp (l,m,h)\t", v.calculateCp())
        print()

if __name__ == '__main__':
    main()
