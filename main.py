#! python3.9

import argparse
import datetime
import math
import json
import sys
import requests

# consts
# song duration + slack, in ms
DURATION = 130000
# multiplier from 3f to 200
DEFAULT_MULT = 4/3
# cp earn rate
CPEP_RATE = 20

# vars
SERVER = 1
EVENT = 153
INTERVAL = 60000
# startTime = 1605834126000 # e97
START_TIME = 1652230800000 # e153
IS_FILE = True

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
    def __init__(self, uid: str, name: str, rawTsd: list[tuple[int, int]]):
        self.uid = uid
        self.name = name

        # rawTsd (absolute time, value)
        # tsd (normalized time, value)
        self.tsd = [ (r[0] - START_TIME, r[1]) for r in rawTsd ]

        # first derivative
        self.d1tsd = []
        # bins
        self.bins = ([], [], [], [])
        # special 0 bin
        self.zeroes = 0

        prev = (0, 0)
        for entry in self.tsd:
            dt = entry[0] - prev[0]
            dv = entry[1] - prev[1]
            prev = entry
            # interpolate in case of big gaps of intervals
            dv = round(dv * INTERVAL / dt)

            if dv == 0:
                self.zeroes += 1
            else:
                if dv < 7000:
                    self.bins[0].append(dv)
                elif dv < 9000:
                    self.bins[1].append(dv)
                elif dv < 17000:
                    self.bins[2].append(dv)
                else:
                    self.bins[3].append(dv)
                # we don't actually need dt
                self.d1tsd.append((dt, dv))

    def getCurrentTime(self) -> int:
        if len(self.tsd) > 0:
            return self.tsd[-1][0]
        else:
            # how?
            return 0

    def getCurrentTotal(self) -> int:
        if len(self.tsd) > 0:
            return self.tsd[-1][1]
        else:
            # how?
            return 0




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
            nt = raw[0] - START_TIME
            dt = nt - prevT
            dv = raw[1] - prevV
            if dt >= INTERVAL * 1.2: # uncertainty
                # theres a skip
                dv = math.ceil(dv * DURATION / dt)

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

    #######
    # *-* #
    #######

    def calcAndPrintCps(self):
        total = self.getTotal()
        dt = self.getDt()
        sums = self.getBinSums()
        counts = self.getBinCounts()
        zeroes = self.zeroes
        sumss = sum(sums)

        # zeroes
        zeroess = (
            round(zeroes * sums[0] / sumss),
            round(zeroes * sums[1] / sumss),
            round(zeroes * sums[2] / sumss),
            round(zeroes * sums[3] / sumss)
        )

        # avgs
        avgs = (
            math.ceil(sums[0] / (counts[0] + zeroess[0]) * DURATION / 60000) if counts[0] > 0 else int(0),
            math.ceil(sums[1] / (counts[1] + zeroess[1]) * DURATION / 60000) if counts[1] > 0 else int(0),
            math.ceil(sums[2] / (counts[2] + zeroess[2]) * DURATION / 60000) if counts[2] > 0 else int(0),
            math.ceil(sums[3] / (counts[3] + zeroess[3]) * DURATION / 60000) if counts[3] > 0 else int(0)
        )

        # cp/ep multiplier
        cpepMult = avgs[1] / avgs[0] if avgs[1] > 0 and avgs[0] > 0 else DEFAULT_MULT

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

        # historical cp
        # epp / 20 - n200 * 200 - n400 * 400 - n800 * 800
        historicalCp = math.ceil(sums[0] / CPEP_RATE) - (counts[1] * 200) - (counts[2] * 400) - (counts[3] * 800)

        # extrapolation
        exTotal = total - sumss
        # duration is kind of trash, maybe use zeroes to estimate duration too
        # number of runs
        exCount = (dt / DURATION) - sum(counts)
        exEv = math.ceil(exTotal / exCount)

        # percentage of cp plays
        p200 = round((exEv - ev3f) / (ev200 - ev3f), 4)
        p400 = round((exEv - ev3f) / (ev400 - ev3f), 4)
        p800 = round((exEv - ev3f) / (ev800 - ev3f), 4)

        print('common')
        print('\tdt\t', dt)
        print('\tavgs\t', avgs)
        print('\tsums\t', sums)
        print('\tcounts\t', counts)
        print('\tzeroess\t', zeroess)

        print('hist')
        print('\thistoricalCp\t', historicalCp)
        print('\tsum counts\t', sum(counts))
        print('\tsum count * dur\t', sum(counts) * DURATION)

        print('exs')
        print('\texTotal\t', exTotal)
        print('\texCount\t', exCount)
        print('\texEv\t', exEv)
        print('\tcpepMult\t', cpepMult)
        print('\tevs\t', (ev3f, ev200, ev400, ev800))
        print('\tex%cp\t', (p200, p400, p800))
        print()
        print('\tdt\t', dt)
        print('\tdt / dur\t', math.ceil(dt / DURATION))

        print('avgs\t', avgs)
        print('exEv\t', exEv)

        # cp = count * ((1 - px) * ev3f / 20 - px * x), x E { 200, 400, 800 }
        ev3f20 = math.ceil(ev3f / 20)

        # if negative, that means suboptimal, so just take leftovers / 20
        if p200 > 0:
            cp200 = math.ceil(exCount * ((1 - p200) * ev3f20 - (p200 * 200)))
        else:
            cp200 = exTotal / 20
    
        if p400 > 0:
            cp400 = math.ceil(exCount * ((1 - p400) * ev3f20 - (p400 * 400)))
        else:
            cp400 = exTotal / 20
    
        if p800 > 0:
            cp800 = math.ceil(exCount * ((1 - p800) * ev3f20 - (p800 * 800)))
        else:
            cp800 = exTotal / 20

        cp200 += historicalCp
        cp400 += historicalCp
        cp800 += historicalCp

        cp200 = max(cp200, 0)
        cp400 = max(cp400, 0)
        cp800 = max(cp800, 0)

        cps = (cp200, cp400, cp800)

        print('total\t', total)
        print('cps\t', cps)
        print('pot pts\t', (
            math.ceil(cps[0] * ev800 / 800),
            math.ceil(cps[1] * ev800 / 800),
            math.ceil(cps[2] * ev800 / 800)
        ))
        print('pot dt\t', (
            str(datetime.timedelta(seconds = math.floor(cps[0] * DURATION / 800000))),
            str(datetime.timedelta(seconds = math.floor(cps[1] * DURATION / 800000))),
            str(datetime.timedelta(seconds = math.floor(cps[2] * DURATION / 800000)))
        ))


###########
# Helpers #
###########

def loadData(server: int, event: int, interval: int, isFile: bool) -> dict:
    fn = f'./e{event}.json'
    if isFile:
        # test
        f = open(fn, 'r')
        j = json.loads(f.read())
    else:
        # prod
        url = f'https://bestdori.com/api/eventtop/data?server={server}&event={event}&mid=0&interval={interval}'
        print(url)
        j = requests.get(url).json()
        f = open(fn, 'w')
        f.write(json.dumps(j))
    return j

def getTop10(points: list) -> dict:
    last10 = points[-10:]
    # verify last 10 has same timestamp
    lastTs = last10[-1]['time']
    for last in last10:
        assert last['time'] == lastTs

    # top 10 users, list of (dt from start, dv from previous, value)
    t10: dict[str, list] = { e['uid'] : [] for e in last10 }
    # aggregate top 10 user data
    for p in points:
        uid = p['uid']
        if uid in t10.keys():
            t = int(p['time'])
            v = int(p['value'])
            t10[uid].append((t, v))

    return t10

def main():
    parser = argparse.ArgumentParser(description = 'todo')
    parser.add_argument('--use-file', dest='is_file', action='store_true')
    args = parser.parse_args(sys.argv[1:])
    print(args)

    # read data
    data = loadData(SERVER, EVENT, INTERVAL, args.is_file)

    # split data
    points = data['points']
    userNames = { u['uid'] : u['name'] for u in data['users'] }

    t10 = getTop10(points)

    userData = { k : UserData(k, userNames[k], v) for k, v in t10.items() }

    for k, v in userData.items():
        print('user\t', k, userNames[k])
        v.calcAndPrintCps()
        print()

if __name__ == '__main__':
    main()
