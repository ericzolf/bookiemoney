"""
Bookiemoney module to read statements and their transactions from an input file
"""

import csv
import logging
import os
import re
import yaml

# identifier for accounts without identifier
NO_ACCOUNT_UID = 'NOIDENTIFIER'


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
        elif ('ignore' in cfg
                and row[cfg['ignore']['key']] == cfg['ignore']['value']):
            logging.warning("Ignoring transaction '{tr}'".format(tr=row))
            continue
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
    recursive function to parse fields in a CSV dictionary

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
