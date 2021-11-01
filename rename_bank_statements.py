#!/usr/bin/python
# Example: pyrename \
#		[A-Za-z]\*.* \
#		'(?P<head>.*)_(?P<d>[0-3][0-9])-(?P<m>[01][0-9])-(?P<y>[12][09][0-9][0-9])_(?P<tail>.*)' \
#		'{y}-{m}-{d}_{head}_{tail}'

import argparse
import datetime
import glob
import logging
import os
import re
import yaml


def parse_arguments():
    """
    Define and parse arguments given on the command line

    Returns a namespace object as returned by parse_args()
    """
    parser = argparse.ArgumentParser(
        description='Rename bank statement files according to rules')
    parser.add_argument('--flavour', required=True,
                        help='type of the account statement [mandatory]')
    parser.add_argument('--dry-run', '-n', action=argparse.BooleanOptionalAction,
                    help="don't rename, just do as if renaming")
    parser.add_argument('inputs', nargs='+', metavar='statements',
                        help='one or more input statement files')
    parser.add_argument(
        '--loglevel',
        choices=('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'),
        help='which level of messages do you want to see?')
    parser.add_argument('--logfile', help='name of a logfile to send logs to')

    return parser.parse_args()


def read_rename_config(flavour):
    """
    Read the renaming flavour config

    Send back the read config
    """
    flavour_file = os.path.join('ren', flavour + '.yml')
    with open(flavour_file, mode='r') as yfd:
        flavour_config = yaml.safe_load(yfd)

    for rename in flavour_config['renames']:
        rename['from'] = re.compile(rename['from'])

    return flavour_config


# MAIN

args = parse_arguments()

# setup the logging
if args.loglevel:
    num_loglevel = getattr(logging, args.loglevel.upper(), None)
    if args.logfile:
        logging.basicConfig(filename=args.logfile, level=num_loglevel)
    else:
        logging.basicConfig(level=num_loglevel)

all_files = []
for file_glob in args.inputs:
    all_files.extend(glob.glob(file_glob))

flavour_config = read_rename_config(args.flavour)

for from_file in sorted(all_files):
    file_matched = False
    logging.debug("Matching '{fi}'".format(fi=from_file))
    dir_name, file_name = os.path.split(from_file)
    for rename in flavour_config['renames']:
        from_match = rename['from'].fullmatch(file_name)
        if from_match:
            group_dict = from_match.groupdict()
            if 'epoch' in group_dict:
                group_dict['epoch'] = datetime.datetime.fromtimestamp(
                    int(group_dict['epoch']))
            to_file = os.path.join(dir_name,
                                   rename['to'].format(**group_dict))
            logging.info("Renaming from '{ff}' to '{tf}'".format(
                ff=from_file, tf=to_file))
            if not args.dry_run:
                os.rename(from_file, to_file)
            file_matched = True
            break
    if not file_matched:
        logging.debug("File '{fi}' couldn't be matched".format(fi=from_file))
