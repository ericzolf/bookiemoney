#!/usr/bin/python

import argparse
import babel.numbers as babelnum
import babel.dates as babeldate
import csv
import locale
import logging
import multiprocessing
import os
import re
import sys
import yaml


# Babel doesn't offer the function to get the 3 letters code from a
# currency symbol, so we need to do it ourselves
CURRENCY_MAP = {babelnum.get_currency_symbol(x): x
                for x in babelnum.list_currencies()
                if x != babelnum.get_currency_symbol(x)}

# maximum possible number of transactions each day
DAILY_TRANSACTIONS = 10000
# identifier for accounts without identifier
NO_ACCOUNT_UID = 'NOIDENTIFIER'


def parse_arguments():
    """
    Define and parse arguments given on the command line

    Returns a namespace object as returned by parse_args()
    """
    parser = argparse.ArgumentParser(
        description='Combine multiple bank statements into one file')
    parser.add_argument('--out', '-o', required=True,
                        help='name of the output file [mandatory]')
    parser.add_argument('--flavour-in', required=True,
                        help='type of the account statement [mandatory]')
    parser.add_argument('--flavour-out', required=True,
                        help='type of the output file [mandatory]')
    parser.add_argument('--plug-gaps', action=argparse.BooleanOptionalAction,
                        help='plug gaps in balance between transactions')
    parser.add_argument('inputs', nargs='+', metavar='statements',
                        help='one or more input statement files')
    parser.add_argument(
        '--loglevel',
        choices=('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'),
        help='which level of messages do you want to see?')
    parser.add_argument('--logfile', help='name of a logfile to send logs to')

    return parser.parse_args()


def read_statement_file(file, flavour):
    """
    Reads a statement file of a given flavour (aka bank)

    Returns a dictionary of dictionaries, where the key is an account ID
    and each sub-dictionary describes the statement and its transactions
    for the given account.

    Note that there is only more than one key if the file contains statements
    for more than one account.
    """

    # this is a dictionary of dictionaries, where each key is an account ID
    # and the value is a dictionary describing the file
    accounts = {}

    logging.info("Reading input file '{fi}'".format(fi=file))

    # the extension of the file gives us the file type
    extension = os.path.splitext(file)[1].lstrip('.').lower()

    # we read accordingly the flavour's configuration file
    flavour_file = os.path.join('in', extension, flavour + '.yml')
    with open(flavour_file, mode='r') as yfd:
        flavour_config = yaml.safe_load(yfd)

    account_uid = flavour_config['identifier']

    # then we match the config line by line with the statement's content
    lines_reader = LinesReader(file, flavour_config['encoding'])

    file_dict = {'file': file, 'flavour': flavour_config}
    for cfg_line in flavour_config['lines']:
        if cfg_line['type'] == 'match':
            result = parse_match(lines_reader, cfg_line)
        elif cfg_line['type'] == 'csv':
            result = parse_csv(lines_reader, cfg_line)
        if result:  # we consider each config line optional
            # handling the presence of multiple accounts in the same
            # file but differentiating them by name
            if (account_uid in result
                    and account_uid in file_dict
                    and result[account_uid] != file_dict[account_uid]):
                file_dict['account_uid'] = file_dict[account_uid]
                accounts[file_dict['account_uid']] = file_dict
                file_dict = {'file': file, 'flavour': flavour_config}
            file_dict |= result
    # then handle the remaining results after the file has been read
    if account_uid in file_dict:
        file_dict['account_uid'] = file_dict[account_uid]
    else:
        file_dict['account_uid'] = NO_ACCOUNT_UID
    accounts[file_dict['account_uid']] = file_dict

    return accounts


def parse_match(lr, cfg):
    """
    Try to match the matching config description with the first non-empty line
    of the line reader 'lr'.

    Returns a dictionary of groups matched by the pattern given in the config.
    The dictionary returned is empty if the pattern doesn't match, if the
    pattern doesn't define group names, or if the config states to skip the
    line.
    """

    try:
        next_line = next(lr)
    except StopIteration:
        return {}

    # try to match line with pattern
    result = re.fullmatch(cfg['pattern'], next_line)
    if result:
        if cfg.get('skip', False):
            return {}
        else:
            return result.groupdict()
    else:  # put back the line on the heap if it doesn't match
        lr.step_back()
        return {}


class LinesReader:
    """
    Wrapper around a file object providing lines until max_read

    We need this wrapper because csv.DictReader resp. 'next()' blocks
    the usage of fd.tell() which we need to step back
    """
    def __init__(self, file, encoding):
        with open(file, mode='r', encoding=encoding) as fd:
            self.lines = list(x.strip() for x in fd.readlines())
        self.max = len(self.lines)
        self.line = -1

    def __next__(self):
        self.line += 1
        while self.line < self.max and not self.lines[self.line]:
            self.line += 1
        if self.line < self.max:
            return self.lines[self.line]
        else:
            raise StopIteration

    def __iter__(self):
        return self

    def step_back(self, back=1):
        self.line -= back


def parse_csv(lr, cfg):
    """
    Try to match the CSV config description with the first non-empty line
    of the line reader 'lr' and following ones.

    Returns a dictionary with the 'transactions' key, the value being a
    list of transactions matched by the CSV config.
    The dictionary returned is empty if the config states to skip the
    CSV line(s).
    """

    transactions = []
    try:
        next_line = next(lr)
    except StopIteration:
        return {}
    header_reader = csv.reader((next_line,), **cfg['dialect'])
    for header in header_reader:
        pass  # there is only one line
    reader = csv.DictReader(lr, header, **cfg['dialect'])
    for row in reader:
        if None in row or row[header[-1]] is None:
            # the line didn't parse correctly, end of the csv...
            lr.step_back()
            break
        else:
            if 'map' in cfg:
                row |= map_fields(row, cfg['map'])
            transactions.append(row)

    if cfg.get('skip', False):
        return {}

    if cfg.get('reverse', False):
        transactions.reverse()

    return {'transactions': transactions}


def map_fields(fields_dict, map_cfg):
    """
    recursive function to parse fields in a dictionary

    parsing is done according to a mapping config
    """
    parsed_dict = {}
    for line in map_cfg:
        # try to match line with pattern
        # the pattern field can be a single pattern or a list of such
        if isinstance(line['pattern'], str):
            patterns = (line['pattern'],)
        else:  # we assume a list
            patterns = line['pattern']
        # the first matching pattern wins
        for pattern in patterns:
            result = re.fullmatch(pattern, fields_dict[line['key']])
            if result:
                break
        if result:
            parsed_dict |= result.groupdict()
            if 'then' in line:
                parsed_dict |= map_fields(fields_dict, line['then'])
        elif 'else' in line:  # exclusive or
            parsed_dict |= map_fields(fields_dict, line['else'])

    return parsed_dict


def clean_account_statement(account_statement):
    account_file = os.path.basename(account_statement['file'])
    flavour_config = account_statement['flavour']
    account_uid = account_statement['account_uid']

    if not account_statement['transactions']:
        logging.warning(
            "Account file '{af}' didn't contain transactions "
            "for account '{ac}'".format(af=account_file, ac=account_uid))
        return account_statement
    else:
        logging.info(
            "Cleaning account file '{af}' for account '{ac}'".format(
                af=account_file, ac=account_uid))
    # determine the default currency from, in decreasing order of priority:
    # 1. the file's account currency
    # 2. the currency defined by the flavour config
    # 3. the default currency for the locale defined by the flavour config
    default_currency = clean_value(
        'account_currency',
        account_statement.get(
            'account_currency', flavour_config.get(
                'currency', get_locale_currency_code(
                    flavour_config.get('locale')))),
        flavour_config)
    for file_key in account_statement:
        if file_key != 'transactions':
            account_statement[file_key] = clean_value(
                file_key, account_statement[file_key], flavour_config)
    for line in account_statement['transactions']:
        for field in line:
            line[field] = clean_value(field, line[field],
                                      flavour_config)
        line['transaction_account_uid'] = account_uid
        # it would be nicer to have it configurable but I couldn't find
        # a simple way to express it
        # basically, some banks/tools only consider a counterpart and
        # don't document between account owner and counterpart,
        # who is the originator resp. the receiver
        if 'transaction_counterpart_name' not in line:
            if ('transaction_originator_name' in line
                    and 'transaction_receiver_name' in line):
                if line['transaction_amount'] > 0:
                    line['transaction_counterpart_name'] = line[
                        'transaction_originator_name']
                else:
                    line['transaction_counterpart_name'] = line[
                        'transaction_receiver_name']
            elif 'transaction_presenter_name' in line:
                line['transaction_counterpart_name'] = line[
                    'transaction_presenter_name']
            elif 'transaction_receiver_name' in line:
                line['transaction_counterpart_name'] = line[
                    'transaction_receiver_name']
            elif 'transaction_originator_name' in line:
                line['transaction_counterpart_name'] = line[
                    'transaction_originator_name']
        if 'transaction_currency' not in line:
            line['transaction_currency'] = default_currency
        if 'transaction_balance_currency' not in line:
            line['transaction_balance_currency'] = default_currency

        # same principle, not all banks make the difference between
        # booking and value dates
        if 'transaction_date' not in line:
            if 'transaction_value_date' in line:
                line['transaction_date'] = line[
                    'transaction_booking_date']
            else:  # it's better to fail here so we don't check
                line['transaction_date'] = line[
                    'transaction_value_date']

    # the new account balance value is the balance value of the last
    # transaction in the file
    if ('transaction_balance_amount'
            not in account_statement['transactions'][-1]
            and 'account_new_balance_amount' in account_statement):
        oldline = account_statement['transactions'][-1]
        oldline['transaction_balance_amount'] = account_statement[
            'account_new_balance_amount']
        for line in account_statement['transactions'][-2::-1]:
            if 'transaction_balance_amount' not in line:
                line['transaction_balance_amount'] = (
                        oldline['transaction_balance_amount']
                        - oldline['transaction_amount'])
            oldline = line
    # the old account balance value is the balance value _before_
    # the first transaction in the file
    elif ('transaction_balance_amount'
            not in account_statement['transactions'][0]
            and 'account_old_balance_amount' in account_statement):
        oldline = account_statement['transactions'][0]
        oldline['transaction_balance_amount'] = (
                account_statement['account_old_balance_amount']
                + oldline['transaction_amount'])
        for line in account_statement['transactions'][1:]:
            if 'transaction_balance_amount' not in line:
                line['transaction_balance_amount'] = (
                        oldline['transaction_balance_amount']
                        + line['transaction_amount'])
            oldline = line

    return account_statement


def clean_value(key, value, cfg):
    """
    Clean a value according to its type defined by the key's suffix

    Returns the cleaned value
    """
    if key.endswith('_amount'):
        return babelnum.parse_decimal(value, locale=cfg['locale'])
    elif key.endswith('_currency'):
        return CURRENCY_MAP.get(value, value)
    elif key.endswith('_date'):
        return babeldate.parse_date(value, locale=cfg['locale'])
    elif key.endswith('_payment_type') and 'payment_types' in cfg:
        return cfg['payment_types'].get(value, value)

    return value


def combine_statement_files(statement):
    clean_statement = {}
    # all statement files must have the same account unique ID, hence we take
    # the first one
    logging.info("Handling account '{ac}'".format(
        ac=statement[0]['account_uid']))

    for file in statement:
        logging.info(
            "Combining {tn} transactions from statement file '{sf}'".format(
                tn=len(file['transactions']),
                sf=os.path.basename(file['file'])))
        transaction_uid = TransactionUid()
        for transaction in file['transactions']:
            uid = transaction_uid(transaction['transaction_date'])
            transaction['transaction_uid'] = uid
            clean_statement[uid] = transaction

    # TODO identify gaps using the balance values - need to consider existing
    #      entries in the target file at a later stage
    # sorted_keys_list = sorted(list(clean_statement.keys()))

    # we return a properly sorted dictionary
    return dict(sorted(clean_statement.items()))


class TransactionUid():
    """
    Returns a unique ID for a given account taking a date

    This works under the assumption that the order of the transactions within
    a day remains stable. This is of course only unique within _one_ account.
    """
    def __init__(self):
        self.index = 1
        self.datenr = 0

    def __call__(self, tdate):
        datenr = DAILY_TRANSACTIONS * (
            tdate.year * 10000 + tdate.month * 100 + tdate.day)
        if datenr == self.datenr:
            self.index += 1
        else:
            self.index = 1
            self.datenr = datenr
        return datenr + 10 * self.index


def output_statement_files(statement_files, out_file, flavour, plug_gaps):
    transactions = combine_statement_files(statement_files)
    output_combined_statement(transactions, out_file, flavour, plug_gaps)


def output_combined_statement(statement, out_file, flavour, plug_gaps):

    # an output file can have a placeholder for the account unique ID
    if "{}" in out_file:
        # all transactions must have the same account UID so we don't care
        # and take the first one
        out_file = out_file.format(
            next(iter(statement.values()))['transaction_account_uid'])

    # the extension of the file gives us the file type
    extension = os.path.splitext(out_file)[1].lstrip('.').lower()

    # we read the flavour's configuration file
    flavour_file = os.path.join('out', extension, flavour + '.yml')
    with open(flavour_file, mode='r') as yfd:
        flavour_config = yaml.safe_load(yfd)

    # normalize values to a list
    normalize_fields(flavour_config['fields'])
    fields = list(flavour_config['fields'].keys())

    if plug_gaps:
        statement = plug_gaps_in_statement(statement)

    logging.info("Writing {tr} transactions to output file '{of}'".format(
        tr=len(statement), of=out_file))
    logging.debug(yaml.dump(flavour_config))

    with open(out_file, 'w', newline='') as csvfile:
        if isinstance(flavour_config['dialect'], str):
            writer = csv.DictWriter(csvfile, fieldnames=fields,
                                    dialect=flavour_config['dialect'])
        else:  # the dialect is a dictionary of CSV options
            writer = csv.DictWriter(csvfile, fieldnames=fields,
                                    **flavour_config['dialect'])
        if flavour_config.get('header', True):
            writer.writeheader()
        for uid in statement:
            row = {}
            for field in fields:
                value = get_field_value(field, statement[uid],
                                        flavour_config['fields'][field],
                                        flavour_config.get('locale'))
                row[field] = value
            logging.debug(row)
            writer.writerow(row)


def normalize_fields(fields):
    for key in fields:
        if fields[key] is None:
            fields[key] = {'value': ['$' + key, '']}
        if not isinstance(fields[key]['value'], list):
            fields[key]['value'] = (fields[key]['value'],)


def get_field_value(field, transaction, field_map, locale=None):
    ret_value = None
    for value_map in field_map['value']:
        try:
            if '{' in value_map:  # we assume a format
                ret_value = value_map.format(**transaction)
            elif value_map.startswith('$'):
                ret_value = transaction[value_map[1:]]
            else:  # either a key name or a plain string
                ret_value = value_map
            break
        except KeyError as exc:
            last_exc = exc
            continue

    if ret_value is None:  # nothing did fit
        raise KeyError(
            "Nothing matched a value for '{fi}' in '{tr}', "
            "last error is '{ex}'".format(
                fi=field, tr=transaction, ex=last_exc))

    # once we have a value, we can first map it, then format it
    if 'map' in field_map:
        ret_value = field_map['map'][ret_value]
    # TODO formatting of the return value
    return ret_value


def get_locale_currency_code(given_locale=None):
    """
    Returns the 3-letters currency code of the given locale or the current one
    """
    curr_locale = locale.getlocale()
    if given_locale:
        locale.setlocale(locale.LC_ALL, (given_locale, curr_locale[1]))
    else:
        locale.setlocale(locale.LC_ALL, curr_locale)
    currency = locale.localeconv()['int_curr_symbol']
    # else we might not be able to find back the current locale
    locale.setlocale(locale.LC_ALL, curr_locale)
    return currency


def plug_gaps_in_statement(statement):
    old_balance = old_uid = 0
    gaps = {}
    for uid in statement:
        new_balance = statement[uid]['transaction_balance_amount']
        gap_amount = new_balance - (
            old_balance + statement[uid]['transaction_amount'])
        if gap_amount != 0:
            gap_uid = uid - uid % DAILY_TRANSACTIONS - 1
            logging.warning(
                "Adding gap plugging transaction '{gt}' of amount {ga} "
                "between old transaction '{ot}' and new one '{nt}'".format(
                    gt=gap_uid, ga=gap_amount, ot=old_uid, nt=uid))
            gaps[gap_uid] = {
                'transaction_account_uid': statement[uid][
                    'transaction_account_uid'],
                'transaction_uid': gap_uid,
                'transaction_amount': gap_amount,
                'transaction_currency': statement[uid]['transaction_currency'],
                'transaction_balance_amount': old_balance + gap_amount,
                'transaction_balance_currency': statement[uid][
                    'transaction_balance_currency'],
                'transaction_payment_type': 'plug_gap',
                'transaction_counterpart_name': 'plug_gap',
                'transaction_details': "PLUG GAP between {ot} and {nt}".format(
                    ot=old_uid, nt=uid)
            }
        old_uid = uid
        old_balance = new_balance

    # we return a properly sorted dictionary
    return dict(sorted((statement | gaps).items()))


# MAIN

args = parse_arguments()

# setup the logging
if args.loglevel:
    num_loglevel = getattr(logging, args.loglevel.upper(), None)
    if args.logfile:
        logging.basicConfig(filename=args.logfile, level=num_loglevel)
    else:
        logging.basicConfig(level=num_loglevel)

# create a list of input_parameters we can use with pool.starmap
input_parameters = list((x, args.flavour_in) for x in args.inputs)

logging.debug("Input parameters are '{ip}'".format(ip=input_parameters))

# read the files, clean them and split them into individual accounts
with multiprocessing.Pool() as pool:
    accounts = pool.starmap(read_statement_file, input_parameters)
    statements = []
    for account_file in accounts:
        statements.extend(account_file.values())
    clean_statements = pool.map(clean_account_statement, statements)

account_statements = {}
for statement in clean_statements:
    account_uid = statement['account_uid']
    if account_uid in account_statements:
        account_statements[account_uid].append(statement)
    else:
        account_statements[account_uid] = [statement, ]

# make sure we detect if output files could get overwritten
if len(account_statements) > 1 and '{}' not in args.out:
    logging.critical(
        "Out file {of} doesn't contain {{}}. That would mean overwriting some "
        "output as there is more than one account in the input files".format(
            of=args.out))
    sys.exit(1)

# there might be more than one account in a file at some banks
with multiprocessing.Pool() as pool:
    statement_parameters = list(
        (statement, args.out, args.flavour_out, args.plug_gaps)
        for statement in account_statements.values())
    result = pool.starmap_async(output_statement_files, statement_parameters)
    result.wait()
if result.successful():
    logging.info("Everything went well, "
                 "{cs} combined statement file(s) written".format(
                     cs=len(statement_parameters)))
    sys.exit(0)
else:
    logging.error("Something went wrong, check previous errors")
    sys.exit(1)
