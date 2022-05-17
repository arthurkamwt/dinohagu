#! python3.9

import argparse
import datetime
import json
import os
import sys
import time
import requests

# consts
# multiplier from multi to multi, 200, 400, 800
DEFAULT_MULT = (1, 4/3, 8/3, 16/3)
# cp earn rate
CPEP_RATE = 20
INTERVAL = 60000

# vars
SERVER = 1
EVENT = 153
START_TIME = 1652230800000 # e153

HOOK = os.environ.get('HOOK')

class UserData:
    def __init__(self, uid: str, name: str, raw: list[tuple[int, int]]):
        self.uid = uid
        self.name = name

        # rawTsd (absolute time, value)
        # tsd (normalized time, value)
        self.tsd = [ (r[0] - START_TIME, r[1]) for r in raw ]

        # save debug
        # f = open('tsd', 'w')
        # f.writelines([f'{x[0]}\t{x[1]}\n' for x in self.tsd])
        # f.close()

        # first derivative
        self.d1tsd = []
        # bins
        self.bins = ([], [], [], [])

        games = 0

        # populate
        accum = 0
        prev = (0, 0)
        for entry in self.tsd:
            dt = entry[0] - prev[0]
            dv = entry[1] - prev[1]
            prev = entry

            if dv == 0:
                accum += dt
                continue

            # interpolate in case of big gaps of intervals
            dv = dv if dt < (INTERVAL * 1.2) else round(dv * duration / (dt + accum))

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
            games += 1
            accum = 0

        self.timePerGame = INTERVAL * len(raw) / games

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

def loadData(server: int, event: int, interval: int, isFile: bool) -> dict:
    fn = f'./e{event}.json'
    if isFile:
        # test
        f = open(fn, 'r')
        j = json.loads(f.read())
        f.close()
    else:
        # prod
        url = f'https://bestdori.com/api/eventtop/data?server={server}&event={event}&mid=0&interval={interval}'
        j = requests.get(url).json()
        # save debug
        # f = open(fn, 'w')
        # f.write(json.dumps(j))
        # f.close()
    return j

def getTop10(points: list, filter: list) -> dict:
    # assumes points are listed in increasing time
    last10 = points[-10:]
    # verify last 10 has same timestamp
    lastTs = last10[-1]['time']
    for last in last10:
        assert last['time'] == lastTs

    # top 10 users, list of (dt from start, dv from previous, value)
    t10: dict[str, list] = { e['uid'] : [] for e in last10 if filter == None or str(e['uid']) in filter }
    # aggregate top 10 user data
    for p in points:
        uid = p['uid']
        if uid in t10.keys():
            t = int(p['time'])
            v = int(p['value'])
            t10[uid].append((t, v))

    return t10

def calculate(userData: UserData, debug: bool):
    totalEp = userData.getCurrentTotal()
    totalTime = userData.getCurrentTime()

    binSums = userData.getBinSums()
    binCounts = userData.getBinCounts()

    # total known points and rounds played
    knownEp = sum(binSums)
    knownTime = len(userData.tsd) * INTERVAL

    ############
    # known cp #
    ############

    # known cp gains from ep + known cp consumed
    knownGain = round(binSums[0] / CPEP_RATE)
    knownLoss = -(binCounts[1] * 200) - (binCounts[2] * 400) - (binCounts[3] * 800)
    knownCp = (
        knownGain,
        knownLoss,
        knownGain + knownLoss
    )

    trueAvgs = tuple(
        (round(binSums[i] / c) if c > 0 else 0) for i, c in enumerate(binCounts)
    )

    for i, a in enumerate(trueAvgs):
        if a != 0:
            avg0 = a * DEFAULT_MULT[i]
            break
    
    avg1 = trueAvgs[1] if trueAvgs[1] > 0 else round(avg0 * DEFAULT_MULT[1])
    avg2 = trueAvgs[2] if trueAvgs[2] > 0 else avg1 * 2
    avg3 = trueAvgs[3] if trueAvgs[3] > 0 else avg2 * 2

    knownAvgs = (avg0, avg1, avg2, avg3)

    ##############
    # unknown cp #
    ##############

    unknownEp = totalEp - knownEp

    # 1. peak, just / 20
    unknownPeakCp = unknownEp / CPEP_RATE

    # 2. at the current rate
    knownEp = sum(binSums)
    unknownLinearCp = knownCp[2] * unknownEp / knownEp

    # 3. estimated percentages
    unknownTime = totalTime - knownTime
    unknownGames = unknownTime / userData.timePerGame
    unknownEpPerGame = unknownEp / unknownGames

    unknownPercent = tuple(
        max(min((unknownEpPerGame - knownAvgs[0]) / (knownAvgs[i] - knownAvgs[0]), 1), 0) for i in [1, 2, 3]
    )

    unknownPercentCp = tuple(
        ((1-unknownPercent[i-1]) * knownAvgs[0] * unknownGames / CPEP_RATE) - (unknownPercent[i-1] * unknownGames * 100 * 2**i) for i in [1, 2, 3]
    )

    # cp results
    unknownCp = (unknownLinearCp, *unknownPercentCp, unknownPeakCp)
    totalCp = tuple(
        max(x + knownCp[2], 0) for x in unknownCp
    )
    projectedEpGain = tuple(
        knownAvgs[3] * x / 800 for x in totalCp
    )
    projectedEp = tuple(
        x + totalEp for x in projectedEpGain
    )
    projectedTime = tuple(
        duration * x / 800 for x in totalCp
    )

    LEFT = 16
    MID = 10

    sdata = (
        f'--------\n'
        f'| User |\n'
        f'--------\n'
        f'{"Name":<{LEFT}}{userData.name}\n'
        f'{"Id":<{LEFT}}{userData.uid}\n'
        f'{"Current time":<{LEFT}}{datetime.datetime.fromtimestamp((userData.tsd[-1][0] + START_TIME) / 1000)}\n'
        f'{"Current ep":<{LEFT}}{totalEp}\n'
        f'\n'
    )

    if debug:
        sdata += (
            f'---------\n'
            f'| Debug |\n'
            f'---------\n'
            f'\n'
            f'Total\n'
            f'-----\n'
            f'{"ep":<{LEFT}}{totalEp}\n'
            f'{"time":<{LEFT}}{totalTime}\n'
            f'{"Time/game":<{LEFT}}{datetime.timedelta(seconds = round(duration / 1000))}\n'
            f'\n'
            f'Known\n'
            f'-----\n'
            f'{"ep":<{LEFT}}{knownEp}\n'
            f'{"time":<{LEFT}}{knownTime}\n'
            f'{"Est. time/game":<{LEFT}}{datetime.timedelta(seconds = round(userData.timePerGame / 1000))}\n'
            f'\n'
            f'{"":<{LEFT}}{"Multi":<{MID}}{"cp200":<{MID}}{"cp400":<{MID}}{"cp800":<{MID}}\n'
            f'{"Data points":<{LEFT}}{"".join(str(s).ljust(MID) for s in binCounts)}\n'
            f'{"True averages":<{LEFT}}{"".join(str(s).ljust(MID) for s in trueAvgs)}\n'
            f'{"Known averages":<{LEFT}}{"".join(str(s).ljust(MID) for s in knownAvgs)}\n'
            f'\n'
            f'Unknown\n'
            f'-------\n'
            f'{"ep":<{LEFT}}{unknownEp}\n'
            f'{"time":<{LEFT}}{unknownTime}\n'
            f'{"games":<{LEFT}}{unknownGames}\n'
            f'{"ep/game":<{LEFT}}{unknownEpPerGame}\n'
            f'\n'
            f'{"":<{LEFT}}{"".join(s.ljust(MID) for s in ["Cp200", "Cp400", "Cp800"])}\n'
            f'{"% cp":<{LEFT}}{"".join(str(round(s, 4)).ljust(MID) for s in unknownPercent)}\n'
            f'\n'
        )

    sdata += (
        f'------\n'
        f'| CP |\n'
        f'------\n'
        f'{"":<{LEFT}}{"Gain":<{MID}}{"Loss":<{MID}}{"Net":<{MID}}\n'
        f'{"Known cp":<{LEFT}}{"".join(str(s).ljust(MID) for s in knownCp)}\n'
        f'\n'
        f'{"Est. method":<{LEFT}}{"Linear":<{MID}}{"% Low":<{MID}}{"% Mid":<{MID}}{"% High":<{MID}}{"Peak":<{MID}}\n'
        f'{"Unknown cp":<{LEFT}}{"".join(str(round(s)).ljust(MID) for s in unknownCp)}\n'
        f'\n'
        f'{"Est. avail cp":<{LEFT}}{"".join(str(round(s)).ljust(MID) for s in totalCp)}\n'
        f'{"Projected +ep":<{LEFT}}{"".join(str(round(s)).ljust(MID) for s in projectedEpGain)}\n'
        f'{"Projected ep":<{LEFT}}{"".join(str(round(s)).ljust(MID) for s in projectedEp)}\n'
        f'{"Projected time":<{LEFT}}{"".join(str(datetime.timedelta(seconds = round(s/1000))).ljust(MID) for s in projectedTime)}\n'
    )

    if HOOK != None:
        jdata = json.dumps({
            'content': f'```\n{sdata}```\n'
        })
        response = requests.post(HOOK, headers={'Content-Type':'application/json'}, data=jdata)
        if response.status_code >= 400:
            print(response.json())
    else:
        print(sdata)

def main(isFile, filters, debug, iduration):
    global duration
    duration = iduration if iduration != None else 150000

    # read data
    data = loadData(SERVER, EVENT, INTERVAL, isFile)

    # split data
    points = data['points']
    userNames = { u['uid'] : u['name'] for u in data['users'] }

    t10 = getTop10(points, filters)

    userData = [ UserData(k, userNames[k], v) for k, v in t10.items() ]

    for v in userData:
        calculate(v, debug)
        time.sleep(0.3)

def lambda_handler(event, context):
    if event['rawPath'] != '/':
        return
    if event['requestContext']['http']['method'] != 'GET':
        return
    
    queries = { s.split('=')[0] : s.split('=')[1] for s in event.get('rawQueryString', '').split('&') if len(s) > 0 }

    isFile = False
    if queries.get('filters') != None and len(queries.get('filters')) > 0:
        filters = queries.get('filters').split(',')
    else:
        filters = None
    debug = queries.get('debug') != None

    if queries.get('duration') != None:
        duration = int(queries.get('duration'))
    else:
        duration = None

    print(filters, debug, duration)
    main(False, filters, debug, duration)

    return {
        'statusCode': 200,
        'body': 'ok'
    }

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description = 'todo')
    parser.add_argument('--use-file', dest='is_file', action='store_true')
    parser.add_argument('-d', '--debug', dest='debug', action='store_true')
    parser.add_argument('-dur', '--duration', dest='duration')
    parser.add_argument('-f', '--filter', dest='filters', nargs='*')
    args = parser.parse_args(sys.argv[1:])

    isFile = args.is_file
    filters = args.filters
    debug = args.debug
    duration = int(args.duration) if args.duration != None else None

    print(isFile, filters, debug, duration)
    main(isFile, filters, debug, duration)
