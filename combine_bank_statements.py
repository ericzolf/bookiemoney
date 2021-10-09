#!/usr/bin/python

import argparse
import babel.numbers as babelnum
import babel.dates as babeldate
import collections
import csv
import locale
import logging
import os
import re
import sys
import yaml


CURRENCY_MAP = {babelnum.get_currency_symbol(x): x
                for x in babelnum.list_currencies()
                if x != babelnum.get_currency_symbol(x)}


def parse_arguments():
    parser = argparse.ArgumentParser(
        description='Combine multiple bank statements into one file')
    parser.add_argument('--out', '-o', required=True,
                        help='name of the output file [mandatory]')
    parser.add_argument('--flavour-in', required=True,
                        help='type of the account statement [mandatory]')
    parser.add_argument('--flavour-out', required=True,
                        help='type of the output file [mandatory]')
    parser.add_argument('inputs', nargs='+', metavar='statements',
                        help='one or more input statement files')
    parser.add_argument(
        '--loglevel', choices=('DEBUG','INFO', 'WARNING', 'ERROR', 'CRITICAL'),
        help='which level of messages do you want to see?')
    parser.add_argument('--logfile', help='name of a logfile to send logs to')

    return parser.parse_args()


def read_input_statements(files_list, flavour):

    # this is a dictionary of lists, where each key is an account with a list
    # of file contents, something like:
    # accounts = {
    #   'account1': [file1, file3],
    #   'account2': [file2, file3],
    # }
    accounts = collections.defaultdict(list)

    for file in files_list:
        logging.info("Reading input file '{fi}'".format(fi=file))

        # the extension of the file gives us the file type
        extension = os.path.splitext(file)[1].lstrip('.')

        # we read accordingly the flavour's configuration file
        flavour_file = os.path.join('in', extension, flavour + '.yml')
        with open(flavour_file, mode='r') as yfd:
            flavour_config = yaml.safe_load(yfd)

        account_uid = flavour_config['identifier']

        # Workaround because fd.readline stays stuck at the EOF
        file_size = os.path.getsize(file)

        # then we match the config line by line with the statement's content
        with open(file, mode='r', encoding=flavour_config['encoding']) as fd:
            file_dict = {'file': file, 'config': flavour_config}
            for cfg_line in flavour_config['lines']:
                if cfg_line['type'] == 'match':
                    result = parse_match(fd, cfg_line)
                elif cfg_line['type'] == 'csv':
                    result = parse_csv(fd, cfg_line, file_size)
                if result:  # we consider each config line optional
                    # handling the presence of multiple accounts in the same
                    # file but differentiating them by name
                    if (account_uid in result
                            and account_uid in file_dict
                            and result[account_uid] != file_dict[account_uid]):
                        accounts[file_dict[account_uid]].append(file_dict)
                        file_dict = {'file': file, 'config': flavour_config}
                    file_dict |= result
            # then handle the remaining results after the file has been read
            if account_uid in file_dict:
                accounts[file_dict[account_uid]].append(file_dict)
            else:
                accounts[''].append(file_dict)

    clean_accounts(accounts, flavour_config)
    logging.debug(yaml.dump(accounts))

    return accounts


def parse_match(fd, cfg):

    last_pos, next_line = skip_empty_lines(fd)

    # try to match line with pattern
    result = re.fullmatch(cfg['pattern'], next_line)
    if result:
        if cfg.get('skip', False):
            return {}
        else:
            return result.groupdict()
    else:  # put back the line on the heap if it doesn't match
        fd.seek(last_pos)
        return {}


class LinesReader:
    """
    Wrapper around a file object providing lines until max_read

    We need this wrapper because csv.DictReader resp. 'next()' blocks
    the usage of fd.tell() which we need to step back
    """
    def __init__(self, fd, max_read=29613):
        self.fd = fd
        self.max_read = max_read

    def __next__(self):
        self.last_pos = self.fd.tell()
        # Workaround because fd.readline stays stuck at the EOF
        if self.last_pos >= self.max_read:
            raise StopIteration
        line = self.fd.readline()
        return line

    def __iter__(self):
        return self

    def close(self):
        self.fd.seek(self.last_pos)


def parse_csv(fd, cfg, max_read):

    transactions = []
    last_pos, next_line = skip_empty_lines(fd)
    header_reader = csv.reader((next_line,), **cfg['dialect'])
    for header in header_reader:
        pass  # there is only one line
    lines_reader = LinesReader(fd, max_read)
    reader = csv.DictReader(lines_reader, header, **cfg['dialect'])
    for row in reader:
        if None in row or row[header[-1]] is None:
            # the line didn't parse correctly, end of the csv...
            lines_reader.close()
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


def clean_accounts(accounts, flavour_config):
    for account_key in accounts:
        logging.info("Cleaning account '{ac}'".format(ac=account_key))
        for file in accounts[account_key]:
            logging.info("Cleaning account file '{af}'".format(
                af=os.path.basename(file['file'])))
            default_currency = file.get('account_currency',
                                        flavour_config.get('currency',
                                        get_locale_currency_code(
                                            flavour_config.get('locale'))))
            for file_key in file:
                if file_key != 'transactions':
                    file[file_key] = clean_value(file_key, file[file_key],
                                                 flavour_config)
            for line in file['transactions']:
                for field in line:
                    line[field] = clean_value(field, line[field],
                                              flavour_config)
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

            # if no transactions were found in this file, we're finished with it
            if not file['transactions']:
                logging.warning("There is no transaction in file '{fi}'".format(
                    fi=os.path.basename(file['file'])))
                logging.debug(file)
                continue

            # the new account balance value is the balance value of the last
            # transaction in the file
            if ('transaction_balance_amount' not in file['transactions'][-1]
                    and 'account_new_balance_amount' in file):
                oldline = file['transactions'][-1]
                oldline['transaction_balance_amount'] = file[
                    'account_new_balance_amount']
                for line in file['transactions'][-2::-1]:
                    if 'transaction_balance_amount' not in line:
                        line['transaction_balance_amount'] = (
                                oldline['transaction_balance_amount']
                                - oldline['transaction_amount'])
                    oldline = line
            # the old account balance value is the balance value _before_
            # the first transaction in the file
            elif ('transaction_balance_amount' not in file['transactions'][0]
                    and 'account_old_balance_amount' in file):
                oldline = file['transactions'][0]
                oldline['transaction_balance_amount'] = (
                        file['account_old_balance_amount']
                        + oldline['transaction_amount'])
                for line in file['transactions'][1:]:
                    if 'transaction_balance_amount' not in line:
                        line['transaction_balance_amount'] = (
                                oldline['transaction_balance_amount']
                                + line['transaction_amount'])
                    oldline = line


def clean_value(key, value, cfg):
    if key.endswith('_amount'):
        return babelnum.parse_decimal(value, locale=cfg['locale'])
    elif key.endswith('_currency'):
        return CURRENCY_MAP.get(value, value)
    elif key.endswith('_date'):
        return babeldate.parse_date(value, locale=cfg['locale'])
    elif key.endswith('_payment_type') and 'payment_types' in cfg:
        return cfg['payment_types'].get(value, value)

    return value


def skip_empty_lines(fd):

    # skip any empty line
    last_pos = fd.tell()
    next_line = fd.readline().strip()
    while not next_line:
        last_pos = fd.tell()
        next_line = fd.readline().strip()
    return last_pos, next_line


def combine_statement_files(statement):
    clean_statement = {}

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
        datenr = (tdate.year * 10000 + tdate.month * 100 + tdate.day) * 10000
        if datenr == self.datenr:
            self.index += 1
        else:
            self.index = 1
            self.datenr = datenr
        return datenr + 10 * self.index


def output_combined_statement(statement, out_file, key, flavour):

    # an output file can have a placeholder for the key
    if "{}" in out_file:
        out_file = out_file.format(key)

    # the extension of the file gives us the file type
    extension = os.path.splitext(out_file)[1].lstrip('.')

    # we read the flavour's configuration file
    flavour_file = os.path.join('out', extension, flavour + '.yml')
    with open(flavour_file, mode='r') as yfd:
        flavour_config = yaml.safe_load(yfd)

    # normalize values to a list
    normalize_fields(flavour_config['fields'])
    fields = list(flavour_config['fields'].keys())

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


# MAIN

args = parse_arguments()

# setup the logging
if args.loglevel:
    num_loglevel = getattr(logging, args.loglevel.upper(), None)
    if args.logfile:
        logging.basicConfig(filename=args.logfile, level=num_loglevel)
    else:
        logging.basicConfig(level=num_loglevel)

account_statements = read_input_statements(args.inputs, args.flavour_in)

# make sure we detect if output files could get overwritten
if len(account_statements) > 1 and '{}' not in args.out:
    logging.critical(
        "Out file {of} doesn't contain {{}}. That would mean overwriting some "
        "output as there is more than one account in the input files".format(
            of=args.out))
    sys.exit(1)

# there might be more than one account in a file at some banks
for key in account_statements:
    logging.info("Handling account '{ac}'".format(ac=key))
    statement = combine_statement_files(account_statements[key])
    output_combined_statement(statement, args.out, key, args.flavour_out)
