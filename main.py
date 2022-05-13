#! python3.9

import argparse
import datetime
import json
import os
import sys
import requests

# consts
# song duration + slack, in ms
DEFAULT_DURATION = 135000
# multiplier from 3f to 200
DEFAULT_MULT = 4/3
# cp earn rate
CPEP_RATE = 20
INTERVAL = 60000
DATE_TIME_FORMAT = '%Y-%m-%d %H:%M:%S %z'

# vars
SERVER = 1
EVENT = 153
START_TIME = 1652230800000 # e153

hook = os.environ.get('HOOK')

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
        # populate ^^^
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
    else:
        # prod
        url = f'https://bestdori.com/api/eventtop/data?server={server}&event={event}&mid=0&interval={interval}'
        j = requests.get(url).json()
        f = open(fn, 'w')
        f.write(json.dumps(j))
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
    currentTotal = userData.getCurrentTotal()
    currentTIme = userData.getCurrentTime()

    binSums = userData.getBinSums()
    binCounts = userData.getBinCounts()

    # total known points and rounds played
    histTotal = sum(binSums)
    histCount = sum(binCounts)


    ############
    # known cp #
    ############

    # known cp gains from ep + known cp consumed
    histCp = (
        round(binSums[0] / CPEP_RATE),
        -(binCounts[1] * 200) - (binCounts[2] * 400) - (binCounts[3] * 800)
    )


    ###############
    # extrapolate #
    ###############

    # number of 0/slack to distribute and round down averages
    zeroes = userData.zeroes
    # evenly distribute 0s to each bin based on sample size
    zeroDist = tuple(
        round(zeroes * c / histCount) for c in binCounts
    )

    # estimate the duration of plays
    # reason: start/ 0 0 .. count /end, duration = sample frequency * (#of0s + 1) / 1
    estDuration = INTERVAL * (histCount + zeroes) / histCount
    # if wildly inaccurate, revert to
    # estDuration = DEFAULT_DURATION

    # options:
    # 1. simple average = sum / count
    # 2. average with slack = (sum / (count + zeroes)) * (estDuration / INTERVAL)
    trueAvgs = tuple(
        (round(binSums[i] / c) if c > 0 else 0) for i, c in enumerate(binCounts)
    )

    # histAvgs = trueAvgs
    histAvgs = tuple(
        round(binSums[i] / (c + zeroDist[i]) * (estDuration / INTERVAL) if c > 0 else 0) for i, c in enumerate(binCounts)
    )

    # in case there is no data for any of the averages
    cpepMult = (histAvgs[1] / histAvgs[0]) if histAvgs[1] > 0 and histAvgs[0] > 0 else DEFAULT_MULT

    # avg0, that is 3f / multi
    if (binCounts[0] > 0):
        avg0 = histAvgs[0]
    elif (binCounts[1] > 0):
        avg0 = round(histAvgs[1] / cpepMult)
    elif (binCounts[2] > 0):
        avg0 = round(histAvgs[2] / (cpepMult * 2))
    else:
        avg0 = round(histAvgs[3] / (cpepMult * 4))

    avg1 = round(histAvgs[1] if binCounts[1] > 0 else avg0 * cpepMult)
    avg2 = round(histAvgs[2] if binCounts[2] > 0 else avg1 * 2)
    avg3 = round(histAvgs[3] if binCounts[3] > 0 else avg2 * 2)

    burnAvgs = (avg1, avg2, avg3)

    # estimated total points
    estTotal = currentTotal - histTotal
    # estimated number of plays
    estCount = (currentTIme / estDuration) - histCount
    # estimated average points per game
    estAvg = round(estTotal / estCount)

    # percentage of cp plays
    # if p > 1, there is no way to achieve this score at this cp burn rate, then we assume they tried
    # if p < 0, user did not meet expected multi average, then we assume all points came from multi
    cpCountDist = tuple(
        min(round((estAvg - avg0) / (a - avg0), 4), 1) for a in burnAvgs
    )

    # convenience
    avg0rate = round(avg0 / CPEP_RATE)
    # calculate each estimated cp gains/loss
    d = cpCountDist[0]
    if d > 0:
        estCp0 = (
            round(estCount * ((1 - d) * avg0rate)),
            -round(estCount * d * 200)
        )
    else:
        estCp0 = (round(estTotal / CPEP_RATE), 0)

    d = cpCountDist[1]
    if d > 0:
        estCp1 = (
            round(estCount * ((1 - d) * avg0rate)),
            -round(estCount * d * 400)
        )
    else:
        estCp1 = (round(estTotal / CPEP_RATE), 0)

    d = cpCountDist[2]
    if d > 0:
        estCp2 = (
            round(estCount * ((1 - d) * avg0rate)),
            -round(estCount * d * 800)
        )
    else:
        estCp2 = (round(estTotal / CPEP_RATE), 0)

    estCp = (estCp0, estCp1, estCp2)
    totalCp = tuple(
        max(sum(e) + sum(histCp), 0) for e in estCp
    )
    # convert all cp to points...
    potentialPoints = tuple(
        round(t * avg3 / 800) for t in totalCp
    )
    # ...will take? maybe use DEFAULT_DURATION?
    potentialTime = tuple(
        datetime.timedelta(seconds = round(t * DEFAULT_DURATION / 800000)) for t in totalCp
    )
    # ...which will bring us up to...
    potentialTotal = tuple(
        p + currentTotal for p in potentialPoints
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
        f'{"Current ep":<{LEFT}}{currentTotal}\n'
        f'\n'
    )

    if debug:
        sdata += (
            f'---------\n'
            f'| Debug |\n'
            f'---------\n'
            f'{"Est. duration":<{LEFT}}{datetime.timedelta(seconds = round(estDuration / 1000))}\n'
            f'\n'
            f'{"":<{LEFT}}{"Multi":<{MID}}{"cp200":<{MID}}{"cp400":<{MID}}{"cp800":<{MID}}\n'
            f'{"Data points":<{LEFT}}{"".join(str(s).ljust(MID) for s in binCounts)}\n'
            f'{"Zero distrib":<{LEFT}}{"".join(str(s).ljust(MID) for s in zeroDist)}\n'
            f'{"True averages":<{LEFT}}{"".join(str(s).ljust(MID) for s in trueAvgs)}\n'
            f'{"Averages":<{LEFT}}{"".join(str(s).ljust(MID) for s in histAvgs)}\n'
            f'{"Est. averages":<{LEFT}}{avg0:<{MID}}{"".join(str(s).ljust(MID) for s in burnAvgs)}\n'
            f'\n'
            f'{"Est. ep":<{LEFT}}{estTotal}\n'
            f'{"Est. count":<{LEFT}}{estCount}\n'
            f'{"Est. average":<{LEFT}}{estAvg}\n'
            f'{"Est. % cp":<{LEFT}}{"".join(str(s).ljust(MID) for s in cpCountDist)}\n'
            f'\n'
        )

    sdata += (
        f'------\n'
        f'| CP |\n'
        f'------\n'
        f'{"":<{LEFT}}{"Gains":<{MID}}{"Loss":<{MID}}{"Net":<{MID}}\n'
        f'{"Historical":<{LEFT}}{"".join(str(s).ljust(MID) for s in [*histCp, sum(histCp)])}\n'
        f'\n'
        f'{"Est. low":<{LEFT}}{"".join(str(s).ljust(MID) for s in [*estCp0, sum(estCp0)])}\n'
        f'{"Est. mid":<{LEFT}}{"".join(str(s).ljust(MID) for s in [*estCp1, sum(estCp1)])}\n'
        f'{"Est. high":<{LEFT}}{"".join(str(s).ljust(MID) for s in [*estCp2, sum(estCp2)])}\n'
        f'\n'
        f'{"":<{LEFT}}{"Low":<{MID}}{"Mid":<{MID}}{"High":<{MID}}\n'
        f'{"Est. avail cp":<{LEFT}}{"".join(str(s).ljust(MID) for s in totalCp)}\n'
        f'{"Projected +ep":<{LEFT}}{"".join(str(s).ljust(MID) for s in potentialPoints)}\n'
        f'{"Projected ep":<{LEFT}}{"".join(str(s).ljust(MID) for s in potentialTotal)}\n'
        f'{"Projected time":<{LEFT}}{"".join(str(s).ljust(MID) for s in potentialTime)}\n'
    )

    if hook != None:
        sdata = f'```\n{sdata}```'.replace('\n', '\\n').replace('\t', '\\t')
        jdata = f'{{ "content" : "{sdata}"}}'
        requests.post(hook, headers={'Content-Type':'application/json'}, data=jdata)
    else:
        print(sdata)

def main(isFile, filters, debug):

    # read data
    data = loadData(SERVER, EVENT, INTERVAL, isFile)

    # split data
    points = data['points']
    userNames = { u['uid'] : u['name'] for u in data['users'] }

    t10 = getTop10(points, filters)

    userData = [ UserData(k, userNames[k], v) for k, v in t10.items() ]

    for v in userData:
        calculate(v, debug)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description = 'todo')
    parser.add_argument('--use-file', dest='is_file', action='store_true')
    parser.add_argument('-d', '--debug', dest='debug', action='store_true')
    parser.add_argument('-f', '--filter', dest='filters', nargs='*')
    args = parser.parse_args(sys.argv[1:])

    isFile = args.is_file
    filters = args.filters
    debug = args.debug

    main(isFile, filters, debug)
