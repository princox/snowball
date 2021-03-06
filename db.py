from typing import Tuple, List
from types import FunctionType

from datetime import datetime
from functools import partial
from itertools import repeat
from statistics import mean
from collections import UserDict, namedtuple

from pymongo import MongoClient, ASCENDING, DESCENDING
from bson.objectid import ObjectId

FScore = namedtuple('FScore', ['total_issued_stock', 'profitable', 'cfo'])
YearStat = namedtuple('YearStat', ['year', 'value', 'calculated'])
Quarter = namedtuple('Quarter', ['year', 'number', 'estimated'])
FilterOption = namedtuple('Filter', ['key', 'title', 'morethan', 'value', 'is_boolean'])


YEAR_STAT = Tuple[int, int]
YEAR_FSCORE = Tuple[int, FScore]


client = MongoClient()
db = client.snowball


DIVIDEND_TAX_RATE = 15.40
FUTURE = 10
TARGET_RATE = 15
THIS_YEAR = datetime.now().year
LAST_YEAR = THIS_YEAR - 1


available_filter_options = [
    FilterOption(key='expected_rate', title='기대수익률', morethan=None, value=None, is_boolean=False),
    FilterOption(key='latest_fscore', title='FScore', morethan=None, value=None, is_boolean=False),
    FilterOption(key='future_roe', title='fROE', morethan=None, value=None, is_boolean=False),
    FilterOption(key='expected_rate_by_current_pbr', title='현P기대수익률', morethan=None, value=None, is_boolean=False),
    FilterOption(key='expected_rate_by_low_pbr', title='저P기대수익률', morethan=None, value=None, is_boolean=False),
    FilterOption(key='pbr', title='PBR', morethan=None, value=None, is_boolean=False),
    FilterOption(key='per', title='PER', morethan=None, value=None, is_boolean=False),
    FilterOption(key='countable_last_four_years_roes_count', title='계산가능ROE수', morethan=None, value=None, is_boolean=False),
    FilterOption(key='roe_max_diff', title='ROE최대최소차', morethan=None, value=None, is_boolean=False),
    FilterOption(key='last_four_years_roe_max_diff', title='최근4년ROE최대최소차', morethan=None, value=None, is_boolean=False),
    FilterOption(key='calculable_pbr_count', title='계산가능PBR수', morethan=None, value=None, is_boolean=False),
    FilterOption(key='is_five_years_record_low', title='5년최저PBR(참)', morethan=None, value=None, is_boolean=True),
    FilterOption(key='has_consensus', title='컨센서스있음(참)', morethan=None, value=None, is_boolean=True),
    FilterOption(key='is_positive_consensus_roe', title='컨센서스>fROE(참)', morethan=None, value=None, is_boolean=True),
]


class Filter(UserDict):
    @property
    def filter_options(self):
        return [FilterOption(
            key=o['key'], 
            title=o['title'], 
            morethan=o['morethan'], 
            value=o['value'], 
            is_boolean=o.get('is_boolean', False)) for o in self['options']]


class Stock(UserDict):
    def __hash__(self):
        return hash(frozenset(self.items()))

    @property
    def object_id(self) -> str:
        return self['_id']

    @property
    def current_price(self):
        return self.get('current_price', 0)

    @property
    def price_arrow(self) -> str:
        if self.get('price_diff') == 0:
            return ''
        else:
            return '▲' if self.get('price_diff') > 0 else '▼'

    @property
    def price_color(self) -> str:
        if self.get('price_diff') == 0:
            return 'black'
        else:
            return 'red' if self.get('price_diff') > 0 else 'blue'

    @property
    def price_sign(self) -> str:
        return '+' if self.get('price_diff') > 0 else ''

    @property
    def pbr(self):
        return self.get('pbr')

    @property
    def per(self):
        return self.get('per')

    @property
    def financial_statements_url(self) -> str:
        return "http://companyinfo.stock.naver.com/v1/company/ajax/cF1001.aspx?cmp_cd=%s&fin_typ=0&freq_typ=Y" % (self['code'])

    @property
    def roes(self) -> List[Tuple[int, int or None]]:
        return self.year_stat('ROEs')

    @property
    def pbrs(self) -> List[Tuple[int, int or None]]:
        return self.year_stat('PBRs')

    @property
    def pers(self) -> List[Tuple[int, int or None]]:
        return self.year_stat('PERs')
    
    @property
    def epss(self) -> List[Tuple[int, int or None]]:
        return self.year_stat('EPSs')

    @property
    def countable_roes(self):
        return [roe for roe in self.get('ROEs', []) if roe]

    @property
    def countable_last_four_years_roes_count(self):
        return len(self.last_four_years_roe)

    @property
    def low_pbr(self) -> float:
        try:
            return min([year_pbr[1] for year_pbr in self.year_stat('PBRs', exclude_future=True) if year_pbr[1] > 0])
        except ValueError:
            return 0

    @property
    def high_pbr(self) -> float:
        try: 
            return max([year_pbr[1] for year_pbr in self.year_stat('PBRs', exclude_future=True) if year_pbr[1] > 0])
        except ValueError:
            return 0

    @property
    def mid_pbr(self) -> float:
        return (self.low_pbr + self.get('pbr')) / 2
    
    @property
    def adjusted_eps(self) -> int:
        past_eps = [eps[1] for eps in self.year_stat('EPSs', exclude_future=True)]
        if len(past_eps) < 3:
            return 0
        return int(((past_eps[-1] * 3) + (past_eps[-2] * 2) + past_eps[-3]) / 6)

    @property
    def mid_roe(self) -> float:
        ROEs = self.countable_roes
        return mean([mean(ROEs), min(ROEs)]) if len(ROEs) > 2 else 0    

    @property
    def eps_growth(self) -> float:
        EPSs = self.get('EPSs', [0, 0])
        try:
            return mean([y/x - 1 for x, y in zip(EPSs[:-1], EPSs[1:])]) * 100
        except ZeroDivisionError:
            return 0

    @property
    def has_note(self) -> bool:
        return len(self.get('note', '')) > 0

    @property
    def latest_fscore(self) -> int:
        fscore = self.fscores[-1][1]
        return sum([fscore.total_issued_stock + fscore.profitable + fscore.cfo])

    @property
    def fscores(self) -> List[Tuple[int, FScore]]:
        NPs = self.year_stat('NPs')
        return [(np[0], self.fscore(np[0])) for np in NPs]

    @property
    def mean_per(self) -> float:
        PERs = self.get('PERs', [])
        return mean(PERs) if len(PERs) > 2 else 0

    @property
    def dividend_tax_adjust(self) -> float:
        return self.get('dividend_rate', 0) * (DIVIDEND_TAX_RATE / 100)

    @property
    def last_four_years_roe(self) -> List[int]:
        return [roe[1] for roe in self.year_stat('ROEs') if roe[1] and roe[0] >= (LAST_YEAR - 3) and roe[0] <= LAST_YEAR]

    @property
    def calculated_roe_count(self):
        return len(self.last_four_years_roe)

    @property
    def calculable_pbr_count(self):
        return len([pbr for pbr in self.year_stat('PBRs', exclude_future=True) if pbr[1] > 0])

    @property
    def mean_roe(self) -> float:
        return mean(self.last_four_years_roe) if self.last_four_years_roe else 0

    @property
    def future_roe(self) -> float:
        return self.mean_roe - self.dividend_tax_adjust     

    @property
    def expected_rate(self) -> float:
        return self.calc_expected_rate(self.calc_future_bps, FUTURE)

    @property
    def invest_price(self) -> float:
        future_bps = self.calc_future_bps(FUTURE)
        return int(future_bps / ((1 + (1 * TARGET_RATE / 100)) ** FUTURE))

    @property
    def expected_rate_by_current_pbr(self) -> float:
        return self.calc_expected_rate(self.calc_future_price_current_pbr, FUTURE)

    @property
    def expected_rate_by_low_pbr(self) -> float:
        return self.calc_expected_rate(self.calc_future_price_low_pbr, FUTURE)

    @property
    def expected_rate_by_mid_pbr(self) -> float:
        return self.calc_expected_rate(self.calc_future_price_low_current_mid_pbr, FUTURE)

    @property
    def expected_rate_by_adjusted_future_pbr(self) -> float:
        return self.calc_expected_rate(self.calc_future_price_adjusted_future_pbr, FUTURE)

    @property
    def intrinsic_value(self) -> int:
        return int((self.get('bps', 0) + (self.adjusted_eps * 10)) / 2)

    @property
    def intrinsic_discount_rate(self) -> float:
        return (self.intrinsic_value / self.current_price ** (1.0 / 1) - 1) * 100

    @property
    def peg_current_per(self) -> float:
        return self.get('per', 0) / self.eps_growth if self.eps_growth != 0 else 0

    @property
    def peg_mean_per(self) -> float:
        return self.mean_per / self.eps_growth if self.eps_growth != 0 else 0

    @property
    def roe_max_diff(self) -> float:
        ROEs = self.countable_roes
        return max(ROEs) - min(ROEs) if len(ROEs) > 2 else 0

    @property
    def last_four_years_roe_max_diff(self) -> float:
        try:
            return max(self.last_four_years_roe) - min(self.last_four_years_roe)
        except:
            return 0

    @property
    def QROEs(self):
        return [(Quarter(*qroe[0]), qroe[1]) for qroe in self.get('QROEs', [])]

    @property
    def QBPSs(self):
        return [(Quarter(*qbps[0]), qbps[1]) for qbps in self.get('QBPSs', [])]

    @property
    def QROEs_QBPSs(self):
        return zip(self.QROEs, self.QBPSs)

    @property
    def calculable(self) -> bool:
        return self.get('bps', 0) > 0 and (self.get('adjusted_future_roe', 0) or self.future_roe) > 0

    @property
    def future_bps(self) -> int:
        return self.calc_future_bps(FUTURE)

    @property
    def other_year_stat(self):
        return zip(self.year_stat('BPSs'), self.year_stat('DEPTs'), self.year_stat('CAPEXs'))

    @property
    def is_five_years_record_low(self):
        return self.low_pbr > self.pbr

    @property
    def has_consensus(self):
        return len(self.consensus_roes) > 0

    @property
    def consensus_roes(self):
        return [pair for pair in self.roes if pair[0] > LAST_YEAR]

    @property
    def mean_consensus_roe(self):
        return mean([pair[1] for pair in self.consensus_roes if pair[1]])

    @property
    def is_positive_consensus_roe(self):
        if not self.has_consensus:
            return False
        return self.mean_consensus_roe >= self.future_roe

    def expected_rate_by_price(self, price) -> float:
        return self.calc_expected_rate(self.calc_future_bps, FUTURE, price=price)

    def calc_future_bps(self, future) -> int:
        if not self.calculable:
            return 0
        bps = self.get('bps', 0)
        adjusted_future_roe = self.get('adjusted_future_roe', 0)
        future_roe = adjusted_future_roe or self.future_roe
        return int(bps * ((1 + (1 * future_roe / 100)) ** future))

    def calc_future_price_low_pbr(self, future) -> int:
        return int(self.calc_future_bps(future) * self.low_pbr)

    def calc_future_price_high_pbr(self, future) -> int:
        return int(self.calc_future_bps(future) * self.high_pbr)

    def calc_future_price_current_pbr(self, future) -> int:
        return int(self.calc_future_bps(future) * self['pbr'])

    def calc_future_price_low_current_mid_pbr(self, future) -> int:
        return int(self.calc_future_bps(future) * self.mid_pbr)

    def calc_future_price_adjusted_future_pbr(self, future) -> int:
        return int(self.calc_future_bps(future) * self.get('adjusted_future_pbr', 0))

    def calc_expected_rate(self, calc_bps, future, price=None):
        if not price:
            price = self.current_price
        return ((calc_bps(future) / price) ** (1.0 / future) - 1) * 100

    def ten_year_prices(self) -> List[Tuple[int, float]]:
        price = self.get('my_price', 0)
        if not price:
            return []
        prices = []
        for i in range(1, 11):
            price = price + (price * 0.15)
            prices.append((i, price))
        return prices
    
    def fscore(self, year) -> FScore:
        total_issued_stock = 0
        profitable = 0
        cfo = 0

        TIs = self.get('TIs', [])
        if len(TIs) > 2 and len(set(TIs)) <= 1:
            total_issued_stock = 1
        NPs = self.year_stat('NPs')
        year_profit = [p[1] for p in NPs if p[0] == year]
        if len(year_profit) > 0 and year_profit[0] > 0:
            profitable = 1
        CFOs = self.year_stat('CFOs')
        year_cfo = [c[1] for c in CFOs if c[0] == year]
        if len(year_cfo) > 0 and year_cfo[0] > 0:
            cfo = 1
        
        return FScore(total_issued_stock=total_issued_stock, profitable=profitable, cfo=cfo)

    def year_stat(self, stat, exclude_future=False) -> List[Tuple[int, int]]:
        stats = self.get(stat)
        if not stats:
            return [(0, 0)]
        
        last_year_index = self.get('last_year_index')
        assert(last_year_index is not None)
        
        year = lambda idx: LAST_YEAR - (last_year_index - idx)
        return [(year(idx), value) for idx, value in enumerate(stats) 
            if not exclude_future or year(idx) <= LAST_YEAR]

    def save_record(self):
        starred = self.get('starred', False)
        owned = self.get('owned', False)
        today = datetime.today()
        today = today.replace(hour=0, minute=0, second=0, microsecond=0)
        if not starred and not owned:
            return
        record = {
            'date': today,
            'buy': 0,
            'sell': 0,
            'bps': self.get('bps', 0),
            'current_price': self.current_price,
            'future_roe': self.future_roe,
            'roe': self.get('roe', 0),
            'pbr': self.get('pbr', 0),
            'expected_rate': self.expected_rate,
        }
        records = self.get('records', [])
        print('records', records)
        if len(records) > 0 and records[-1]['date'] == today:
           records[-1] = record
        else:
           records.append(record)
        save_stock({
            'code': self.get('code'),
            'records': records,
        })
    def __str__(self) -> str:
        return '{} : {}'.format(self['title'], self['code'])


def attr_or_key_getter(name, obj):
    try:
        return getattr(obj, name)
    except AttributeError:
        return obj.get(name, 0)
    

def make_filter_option_func(filter_option):
    def filter_option_func(s):
        v = getattr(Stock(s), filter_option.key)
        if filter_option.is_boolean:
            return v
        return v >= filter_option.value if filter_option.morethan else v <= filter_option.value
    return filter_option_func


def all_stocks(order_by='title', ordering='asc', find=None, filter_by_expected_rate=True, filter_bad=True, filter_fscore=False, filter_options=[]) -> List[Stock]:
    dicts = db.stocks.find(find) if find else db.stocks.find()

    filter_funcs = []

    if filter_by_expected_rate:
        filter_by_expected_rate_func = lambda s: (Stock(s).expected_rate > 0 and filter_bad) or (Stock(s).expected_rate < 0 and not filter_bad)
        filter_funcs.append(filter_by_expected_rate_func)
    
    if filter_fscore:
        filter_funcs.append(lambda s: (Stock(s).latest_fscore == 3) and filter_by_expected_rate_func(s))

    for filter_option in filter_options:
        filter_funcs.append(make_filter_option_func(filter_option))

    return sorted([Stock(s) for s in dicts if all(list(map(FunctionType.__call__, filter_funcs, repeat(s))))],
        key=partial(attr_or_key_getter, order_by), reverse=(ordering != 'asc'))


def stock_by_code(code) -> Stock:
    return Stock(db.stocks.find_one({'code': code}))


def save_stock(stock) -> Stock:
    exist = db.stocks.find_one({'code': stock['code']})
    if exist:
        print("update:" ,stock)
        db.stocks.update_one({'code': exist['code']}, {'$set': stock})
    else:
        db.stocks.insert_one(stock)
    return stock_by_code(stock['code'])


def unset_keys(keys_to_unsets):
    for key in keys_to_unsets:
        db.stocks.update({}, {'$unset':{key: 1}}, multi=True)


def all_filters():
    dicts = db.filters.find()
    return [Filter(f) for f in dicts]


def filter_by_id(filter_id) -> Filter:
    return Filter(db.filters.find_one({'_id': ObjectId(filter_id)}))


def save_filter(filter):
    filter_id = filter.get('_id', None)
    if filter_id:
        return db.filters.update_one({'_id': ObjectId(filter_id)}, {'$set': filter}).upserted_id
    else:
        return db.filters.insert_one(filter).inserted_id


def remove_filter(filter_id):
    db.filters.delete_one({'_id': ObjectId(filter_id)})