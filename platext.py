#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Extract and verify payslip information

Usage:
  platext.py (extract | gnucash | verify) <file> [--debug]
  platext.py [--assumptions] verify <file> [--debug]

Commands:
  extract       Outputs payslip as a dict
  gnucash       Outputs payslip in a gnucash-friendly table
  verify        Checks if payslip info are correct

Arguments:
  file          A text file containing the text layer of the .pdf payslip

Options:
  -a --assumptions  Show which assumptions were made at verification
  -d --debug        Show debug messages
"""

import re
import os
import sys
import json
import logging

from math import ceil, floor
from datetime import date
from itertools import count

from common import clean_num, clean_hours, pretty, setup_logging, load_pdf_file, extract_pdf_from_zip

# install these from pip
from docopt import docopt
try:
    from tabulate import tabulate
except ImportError:
    logging.warn("'tabulate' module not found. Using poor table printing.")
    from common import tabulate_poor as tabulate

TEXT_TELEFON = 'Telefon: 225 335 126'
TEXT_SICK = 'Sick payments'
TEXT_11   = '1/1'
TEXT_BASE = 'Base salary'
TEXT_NET  = 'Net salary'
TEXT_HOURS = 'Working hours'
TEXT_VACATION_PAY = 'vacation pay'
TEXT_HOLIDAY_BALANCE = 'Holiday balance'
TEXT_TAXABLE_INCOME  = 'Taxable income'
TEXT_ILLNESS = 'Illness'
TEXT_TAX_BASE = 'Tax base'
TEXT_PARTIAL_TAX_BASE = 'Partial tax base'
TEXT_AVERAGE_EARNINGS = 'Average earnings'

TEXT_TAX_ADVANCE = 'Tax advance'
TEXT_TAX_RELIEF  = 'Tax relief(§35ba)'
TEXT_TAX_AFTER_RELIEF = 'Tax after relief (§35ba)'
TEXT_TAX_INCOME  = 'Tax withheld'
TEXT_TAX_ANNUAL  = 'Annual Tax Reconciliation'
TEXT_TAX_SOCIAL  = 'Social security'
TEXT_TAX_HEALTH  = 'Health insurance'
TEXT_TAX_RELIEF_TP  = 'Tax relief - taxpayer'
TEXT_TAX_MEALS   = 'Deduction - meals'
TEXT_TAX_TRAVEL  = 'Travel Expenses'

taxblock_fields = [
    TEXT_TAX_ADVANCE,
    TEXT_TAX_RELIEF,
    TEXT_TAX_AFTER_RELIEF,
    TEXT_TAX_INCOME,
    TEXT_TAX_ANNUAL,
    TEXT_TAX_SOCIAL,
    TEXT_TAX_HEALTH,
    TEXT_TAX_RELIEF_TP,
    TEXT_TAX_MEALS,
    TEXT_TAX_TRAVEL,
]

fixed_state_holidays = [(1,1), (1,5), (8,5), (5,7), (6,7), (28,9), (28,10), (17,11), (24,12), (25,12), (26,12)]

class IncomeExtractor():

    MEAL_MY_PART = 0.45

    def __init__(self, text):
        self.lines = text.split('\n')

        self.re_holiday      = re.compile("^Holiday [\\d,]+d")
        self.re_unpaid       = re.compile("^Omluv.*")
        self.re_base_salary  = re.compile("^Base salary")
        self.re_bonus        = re.compile("^Bonus CZK")
        self.re_vacation_pay = re.compile("^Summer vacation pay")

        self.res_holidays = [
            self.re_holiday,
            self.re_unpaid,
            self.re_base_salary,
            self.re_bonus,
            self.re_vacation_pay,
        ]

        self.res_bonuses = [
            self.re_bonus,
            self.re_vacation_pay,
        ]

    def find_shifted(self, anchortext, shift):
        """
        Search for a line containing `anchortext`.
        Extract the number from a line shifted by `shift` to the previously found line.
        """
        return self.find_shifted_list(anchortext, [shift])

    def find_shifted_list(self, anchortext, shifts):
        anchor = self.index_in(anchortext)
        return clean_num(
            ''.join(self.lines[anchor + shift] for shift in shifts)
        )

    def find_shifted_hours(self, anchortext, shift):
        ind = self.index_in(anchortext)
        return int(self.lines[ind + shift].split(':')[0])

    def isin(self, text):
        try:
            self.index_in(text)
            return 1
        except:
            return 0

    def index_in(self, needle):
        for i, hay in enumerate(self.lines):
            if needle in hay:
                return i
        else:
            raise KeyError(needle)

    def exception_may(self):
        return self.index_in(TEXT_SICK) < self.index_in(TEXT_TAX_SOCIAL)


    @property
    def taxblock_keys(self):
        keys = []
        for field in taxblock_fields:
            if self.isin(field):
                keys.append(field)
        return keys
    

    @property
    def variable_number(self):
        return len(self.taxblock_keys)

    @property
    def period(self):
        return self.lines[4].split(':')[1]

    @property
    def base(self):
        return self.find_shifted(TEXT_BASE, 4)

    @property
    def bank(self):
        return self.find_shifted(TEXT_11, -2 - 2*self.isin(TEXT_TELEFON))

    @property
    def gross(self):
        return self.find_shifted(TEXT_NET, -3)

    @property
    def net(self):
        return self.find_shifted(TEXT_SICK, -2)

    @property
    def hours_exepected(self):
        return self.find_shifted_hours(TEXT_HOURS, 4)

    @property
    def hours_worked(self):
        return self.find_shifted_hours(TEXT_HOURS, 5)

    @property
    def holidayblock(self):
        ind_base = self.index_in(TEXT_ILLNESS) + 1
        desclist, hourlist, cashlist = [], [], []
        if self.lines[ind_base] != TEXT_BASE:
            ind_base = self.index_in(TEXT_TAX_BASE) + 2
            i = ind_base
            while any(regex.match(self.lines[i]) for regex in self.res_holidays):
                desclist.append(self.lines[i])
                hourlist.append('0:0fake')
                cashlist.append(self.lines[i+2])
                i += 4
        else:
            for i in count(ind_base):
                if not any(regex.match(self.lines[i]) for regex in self.res_holidays):
                    break

            holiday_lines = i - ind_base

            vacation = self.isin(TEXT_VACATION_PAY)

            ind_hol = self.index_in(TEXT_HOLIDAY_BALANCE)
            #ind_partax = self.index_in(TEXT_PARTIAL_TAX_BASE)
            ind_adv = self.index_in(TEXT_TAX_ADVANCE)

            desc_start  = ind_base
            hours_start = ind_hol + 3 + holiday_lines
            #cash_start  = ind_partax + 1
            cash_start  = ind_adv - holiday_lines - 1
            # if not self.lines[ind_adv].startswith(TEXT_TAX_ADVANCE):
            #     cash_start += 1

            desclist = self.lines[desc_start  : desc_start  + holiday_lines]
            hourlist = self.lines[hours_start : hours_start + holiday_lines]
            cashlist = self.lines[cash_start  : cash_start  + holiday_lines]

        return list(zip(desclist, hourlist, cashlist))

    @property
    def hours_holiday(self):
        expr = re.compile("Holiday [\\d,]+d")
        hol_hours = 0
        for desc, hours, money in self.holidayblock:
            if expr.match(desc):
                hol_hours += clean_hours(hours)
        return hol_hours

    @property
    def hours_holiday_list(self):
        # not the same as hours_holiday
        expr = re.compile("Holiday [\\d,]+d")
        hol_hours = []
        for desc, hours, money in self.holidayblock:
            if expr.match(desc):
                hol_hours.append(clean_hours(hours))
        return hol_hours


    @property
    def bonuses(self):
        expr = re.compile("Holiday [\\d,]+d")
        total_bonus = 0
        for desc, hours, money in self.holidayblock:
            if any(expr.match(desc) for expr in self.res_bonuses):
                total_bonus += clean_num(money)
        return total_bonus

    @property
    def average_earnings(self):
        ind_avg = self.index_in(TEXT_AVERAGE_EARNINGS)
        return float(self.lines[ind_avg + 4].replace(',', '.'))
    
        
    @property
    def taxblock(self):
        _taxblock = getattr(self, '_taxblock', None)
        if _taxblock:
            return _taxblock

        keys = self.taxblock_keys
        varnum = self.variable_number

        ind_sick = self.index_in(TEXT_SICK)
        ind_11   = self.index_in(TEXT_11)

        if self.exception_may():
            ind_var1 = ind_sick + 19
        else:
            ind_var1 = ind_sick + 6 + self.isin(TEXT_TAX_ANNUAL)
        
        ind_var2 = ind_11 - 3 - self.isin(TEXT_TELEFON)*2 - varnum

        variables_1 = self.lines[ind_var1 : ind_var2]
        variables_2 = self.lines[ind_var2 : ind_var2 + varnum]

        #make them the same size
        variables_1.extend([''] * (len(variables_2) - len(variables_1)))

        d = {}
        for key, num1, num2 in zip(keys, variables_1, variables_2):
            try:
                d[key] = clean_num(num1 + num2)
            except ValueError:
                raise ValueError("Couldn't extract value for '{}', found '{}' + '{}'".format(
                    key, num1, num2))
        self._taxblock = d
        return self._taxblock

    @property
    def tax_advance(self):
        return self.taxblock.get(TEXT_TAX_ADVANCE)
    
    @property
    def tax_relief(self):
        return self.taxblock.get(TEXT_TAX_RELIEF)
    
    @property
    def tax_income(self):
        return self.taxblock.get(TEXT_TAX_INCOME)

    @property
    def tax_social(self):
        return self.taxblock.get(TEXT_TAX_SOCIAL)

    @property
    def tax_health(self):
        return self.taxblock.get(TEXT_TAX_HEALTH)

    @property
    def tax_meal(self):
        return self.taxblock.get(TEXT_TAX_MEALS)

    @property
    def tax_travel(self):
        return self.taxblock.get(TEXT_TAX_TRAVEL)

    @property
    def tax_recon(self):
        return self.taxblock.get(TEXT_TAX_ANNUAL)

    @property
    def month(self):
        months = {
            'January': 1,
            'February': 2,
            'March': 3,
            'April': 4,
            'May': 5,
            'June': 6,
            'July': 7,
            'August': 8,
            'September': 9,
            'October': 10,
            'November': 11,
            'December': 12,
        }
        name = self.period.split()[0]
        return months[name]

    @property
    def year(self):
        return int(self.period.split()[1])    

    @property
    def state_holidays_workdays(self):
        # counted = 0
        # for day, month in fixed_state_holidays:
        #     wd = date(self.year, month, day).weekday()
        #     if wd not in [5, 6]:
        #         counted += 1
        # return counted
        my_fixed_state_holidays = fixed_state_holidays.copy()
        if self.year == 2016:
            my_fixed_state_holidays.extend([(25,3), (28,3)])

        return sum(
            date(self.year, month, day).weekday() not in [5, 6]
            for day, month in my_fixed_state_holidays if month == self.month)

    def extract_amounts(self):
        return {
            'period': self.period,
            'base': self.base,
            'bank': self.bank,
            'gross': self.gross,
            'net': self.net,
            'tax_advance': self.tax_advance,
            'tax_relief': self.tax_relief,
            'tax_income': self.tax_income,
            'tax_social': self.tax_social,
            'tax_health': self.tax_health,
            'tax_recon':  self.tax_recon,
            'meal_deduction': self.tax_meal,
            'travel_expence': self.tax_travel,
            'hours_exepected': self.hours_exepected,
            'hours_worked': self.hours_worked,
            'hours_holiday': self.hours_holiday,
            'bonuses': self.bonuses,
        }

    def gnucash(self):
        """
        Prints gnucash-friendly payslip report.
        """
        meal_total = round(self.tax_meal / self.MEAL_MY_PART)
        bonuses = self.bonuses if self.bonuses else 0

        FILLER = None

        headers = ['Account', 'To', 'From']
        taxes = [
            ['Income tax', self.tax_income, FILLER],
            ['Social tax', self.tax_social, FILLER],
            ['Health tax', self.tax_health, FILLER],
        ]

        assets = [
            ['Bank', self.bank, FILLER],
            ['Meal tickets', meal_total, FILLER],
        ]

        income = [
            ['Monthly salary', FILLER, self.gross - bonuses],
            ['Meal contribution', FILLER, round(meal_total * (1 - self.MEAL_MY_PART))],
        ]

        if self.tax_travel:
            income.append(['Travel expenses', FILLER, -self.tax_travel])

        if self.bonuses:
            income.append(['Bonuses', FILLER, self.bonuses])
        if self.tax_recon:
            income.append(['Tax reconciliation', FILLER, -self.tax_recon])

        table = assets + taxes + income

        print(tabulate(table, headers, numalign='right'))

class IncomeVerificator():
    def __init__(self, extractor):
        self.ie = extractor
        self.daily_meal = 80
        self.meal_contribution = 0.45
        self.factor_supergross = 1.34
        self.factor_tax_income = 0.15
        self.factor_tax_social = 0.065
        self.factor_tax_health = 0.045
        self.tax_relief = 2070

    def assumptions(self):
        ie = self.ie
        tax_travel = ie.tax_travel if ie.tax_travel else 0
        money = [
            ['Monthly base',    ie.base],
            ['Bonuses',         ie.bonuses],
            ['Travel expences', tax_travel],
            ['Hourly (last 3 mths)', ie.average_earnings],
        ]
        print(tabulate(money, headers=['Assumptions', "Amount [CZK]"]))
        print()

        hours = [
            ['Expected',    ie.hours_exepected, ie.hours_exepected/8],
            ['Worked',      ie.hours_worked,    ie.hours_worked/8],
            ['Holiday',     ie.hours_holiday,   ie.hours_holiday/8],
            ['State holiday workdays', ie.state_holidays_workdays*8, ie.state_holidays_workdays]
        ]

        print(tabulate(hours, headers=['Assumptions', "Hours", "Days"]))
        print()

        print(tabulate([['Daily meal', self.daily_meal]], headers=['Assumptions', 'Amount [CZK]']))
        print()

    def _print_verification_message(self, category, claimed, calculated):
        err_string = 'OK ({})'.format(claimed) if claimed != calculated \
            else '{} (claimed) != {} (calculated)'.format(claimed, calculated)

        print('{}: {}'.format(category, err_string))

    def verify_gross(self):
        ie = self.ie

        holiday_money = sum(round(hours * ie.average_earnings) for hours in ie.hours_holiday_list)
        my_gross = round(ie.hours_worked / ie.hours_exepected * ie.base \
            + holiday_money + ie.bonuses)
        
        return ('Gross', ie.gross, my_gross)

    def verify_taxes(self):
        return [
            self.verify_tax_income_raw(),
            self.verify_tax_income_relief(),
            self.verify_tax_social(),
            self.verify_tax_health(),
        ]

    def verify_tax_income_raw(self):
        ceil100 = lambda x: 100 * ceil(x / 100)
        supergross = ceil100(self.ie.gross * self.factor_supergross)
        my_tax_income = supergross * self.factor_tax_income

        return ('Tax-advance', self.ie.tax_advance, my_tax_income)

    def verify_tax_income_relief(self):
        return ('Tax-income', self.ie.tax_income, self.ie.tax_advance - self.tax_relief)

    def verify_tax_social(self):
        my_tax_social = ceil(self.ie.gross * self.factor_tax_social)
        return ('Tax-social', self.ie.tax_social, my_tax_social)

    def verify_tax_health(self):
        my_tax_health = ceil(self.ie.gross * self.factor_tax_health)
        return ('Tax-health', self.ie.tax_health, my_tax_health)

    def verify_meal(self):
        ie = self.ie
        eligible_days = (ie.hours_worked) / 8 - ie.state_holidays_workdays

        # half-days _are_ paid
        eligible_days = ceil(eligible_days)

        should_meal = eligible_days * self.daily_meal
        my_contrib  = should_meal * self.meal_contribution

        t = ('Meal contrib.', ie.tax_meal, my_contrib)
        return t


    def verify_net(self):
        ie = self.ie
        recon = ie.tax_recon if ie.tax_recon else 0
        taxes = ie.tax_income + ie.tax_social + ie.tax_health + recon
        my_net = ie.gross - taxes

        return ('Net', ie.net, my_net)

    def verify_bank(self):
        ie = self.ie
        tax_travel = ie.tax_travel if ie.tax_travel else 0
        my_bank = ie.net - ie.tax_meal - tax_travel
        return ('Bank', ie.bank, my_bank)

    def _verification_tuple_to_printable(self, result):
        name, claimed, calculated = result
        err_msg = 'FAIL'
        only_warns = ['Meal contrib.',]
        if name in only_warns:
            err_msg = 'WARN'

        status =     'OK' if claimed == calculated else err_msg
        difference = None if claimed == calculated else claimed - calculated
        return [name, status, difference, claimed, calculated]


    def verify(self, assumptions=False):
        if assumptions:
            self.assumptions()

        print("Verification")
        results = [
            self.verify_gross(),
            self.verify_net(),
            self.verify_meal(),
            self.verify_bank(),
        ]
        results.extend(
            self.verify_taxes()
        )

        table   = [self._verification_tuple_to_printable(result) for result in results] 
        headers = ['Test', 'Result', 'Diff', 'Claim', 'Calc.']
        print(tabulate(table, headers))

        self.verify_warnings()

    def verify_warnings(self):
        name, claimed, calculated = self.verify_meal()
        difference = claimed - calculated
        days = -difference / (self.meal_contribution * self.daily_meal)
        if abs(days - round(days)) < 0.4:
            days = round(days)

        if days > 0:
            print("\nWARNING: You are missing {0} days in meal tickets. Maybe {0} sickdays?".format(days))
        elif days < 0:
            print("\nWARNING: you have {} days worth of meal tickets more.".format(-days))

def quickinit():
    filename = 'test_samples/vyp-2016-04-en.txt'
    text = open(filename, 'r').read()
    ie = IncomeExtractor(text)
    return ie

def main():
    args = docopt(__doc__)
    setup_logging(logging.DEBUG if args['--debug'] else logging.WARNING)

    ds = [date(y,m,1) for y in [2015,2016] for m in range(1,13)]
    ds = {d.strftime('%b%y').lower(): 'test_samples/vyp-{}-en.pdf'.format(d.strftime('%Y-%m')) for d in ds}

    filename = args['<file>']

    if filename in ds:
        filename = ds[filename]

    if filename.lower().endswith('.zip'):
        pdfname = extract_pdf_from_zip(filename)
    else:
        pdfname = filename

    try:
        #text = open(filename, 'r').read()
        text = load_pdf_file(pdfname)
    except FileNotFoundError:
        print("File not found: {}".format(pdfname))
        sys.exit(1)

    if filename.lower().endswith('.zip'):
        os.remove(pdfname)

    ie = IncomeExtractor(text)

    if args['extract']:
        result = ie.extract_amounts()
        pretty(result)
    elif args['gnucash']:
        ie.gnucash()
    elif args['verify']:
        iv = IncomeVerificator(ie)
        iv.verify(assumptions=args['--assumptions'])

if __name__ == '__main__':
    main()