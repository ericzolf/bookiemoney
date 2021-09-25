#!/usr/bin/python

import argparse
import collections
import os
import re
import yaml

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
        flavour_file = os.path.join(extension, flavour + '.yml')
        with open(flavour_file, mode='r') as yfd:
            flavour_config = yaml.safe_load(yfd)
        #print(flavour_config)

        # then we match the config line by line with the statement's content
        with open(file, mode='r', encoding=flavour_config['encoding']) as fd:
            file_dict = {'file': file}
            for line in flavour_config['lines']:
                if line['type'] == 'match':
                    result = parse_match(fd, line)
                elif line['type'] == 'csv':
                    result = parse_csv(fd, line)
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

    print(accounts)
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


def parse_csv(fd, cfg):

    last_pos, next_line = skip_empty_lines(fd)
    return {}


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
