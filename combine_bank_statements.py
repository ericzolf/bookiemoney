#!/usr/bin/python

import argparse
import babel.numbers as babelnum
import babel.dates as babeldate
import collections
import csv
import os
import re
import yaml


CURRENCY_MAP = { babelnum.get_currency_symbol(x): x
                 for x in babelnum.list_currencies()
                 if x != babelnum.get_currency_symbol(x) }

def parse_arguments():
    parser = argparse.ArgumentParser(
        description='Combine multiple bank statements into one file')
    parser.add_argument('--out', '-o', required=True,
                        help='name of the output file [mandatory]')
    parser.add_argument('--flavour', required=True,
                        help='type of the account statement [mandatory]')
    parser.add_argument('inputs', nargs='+', metavar='statements',
                        help='one or more input statement files')

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
        # the extension of the file gives us the file type
        extension = os.path.splitext(file)[1].lstrip('.')

        # we read accordingly the flavour's configuration file
        flavour_file = os.path.join('in', extension, flavour + '.yml')
        with open(flavour_file, mode='r') as yfd:
            flavour_config = yaml.safe_load(yfd)

        # Workaround because fd.readline stays stuck at the EOF
        file_size = os.path.getsize(file)

        # then we match the config line by line with the statement's content
        with open(file, mode='r', encoding=flavour_config['encoding']) as fd:
            file_dict = {'file': file}
            for cfg_line in flavour_config['lines']:
                if cfg_line['type'] == 'match':
                    result = parse_match(fd, cfg_line)
                elif cfg_line['type'] == 'csv':
                    result = parse_csv(fd, cfg_line, file_size)
                if result:  # we consider each config line optional
                    # handling the presence of multiple accounts in the same
                    # file but differentiating them by name
                    if ('account_name' in result
                            and 'account_name' in file_dict
                            and result['account_name']
                                != file_dict['account_name']):
                        accounts[file_dict['account_name']].append(file_dict)
                        file_dict = {'file': file}
                    file_dict |= result
            # then handle the remaining results after the file has been read
            if 'account_name' in file_dict:
                accounts[file_dict['account_name']].append(file_dict)
            elif 'account_id' in file_dict:
                accounts[file_dict['account_id']].append(file_dict)
            else:
                accounts[''].append(file_dict)

    clean_accounts(accounts, flavour_config)
    print(yaml.dump(accounts))

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

    lines = []
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
            lines.append(row)

    if cfg.get('skip', False):
        return {}

    if cfg.get('reverse', False):
        lines.reverse()

    return {'lines': lines}


def map_fields(fields_dict, map_cfg):
    """
    recursive function to parse fields in a dictionary according to a mapping config
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
        for file in accounts[account_key]:
            for file_key in file:
                if file_key != 'lines':
                    file[file_key] = clean_value(file_key, file[file_key],
                                                 flavour_config)
            for line in file['lines']:
                for field in line:
                    line[field] = clean_value(field, line[field],
                                              flavour_config)


def clean_value(key, value, cfg):
    if key.endswith('_value'):
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

def cleanup_statements(statement):
    pass


def output_combined_statement(statement, out_file, key):
    pass


# MAIN

args = parse_arguments()

account_statements = read_input_statements(args.inputs, args.flavour)

# there might be more than one account in a file at some banks
for key in account_statements:
    statement = cleanup_statements(account_statements[key])
    output_combined_statement(statement, args.out, key)
