#!/usr/bin/python

import argparse
import logging
import multiprocessing
import sys
import yaml

from bookmo import bm_read_csv as bm_read
from bookmo import bm_clean
from bookmo import bm_write_csv as bm_write


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
    parser.add_argument('--serial', action=argparse.BooleanOptionalAction,
                        help='process serially (makes debugging easier)')
    parser.add_argument('inputs', nargs='+', metavar='statements',
                        help='one or more input statement files')
    parser.add_argument(
        '--loglevel',
        choices=('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'),
        help='which level of messages do you want to see?')
    parser.add_argument('--logfile', help='name of a logfile to send logs to')

    return parser.parse_args()


def serial_starmap(function, params):
    """
    Simulate multiprocessing.Pool().starmap in a serial manner
    """
    return list(function(*x) for x in params)


def serial_map(function, params):
    """
    Simulate multiprocessing.Pool().map in a serial manner
    """
    return list(function(x) for x in params)


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
    if args.serial:
        accounts = serial_starmap(bm_read.read_statement_file, input_parameters)
    else:
        accounts = pool.starmap(bm_read.read_statement_file, input_parameters)
    statements = []
    for account_file in accounts:
        statements.extend(account_file.values())
    # logging.debug("Cleaning statements '{st}'".format(
    #     st=yaml.safe_dump(statements)))
    if args.serial:
        clean_statements = serial_map(bm_clean.clean_account_statement, statements)
    else:
        clean_statements = pool.map(bm_clean.clean_account_statement, statements)

# sort the statements by same account in a dictionary
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

# write now all statements to one output file per account
with multiprocessing.Pool() as pool:
    statement_parameters = list(
        (statement, args.out, args.flavour_out, args.plug_gaps)
        for statement in account_statements.values())
    if args.serial:
        result = serial_starmap(bm_write.output_account_statements,
                                statement_parameters)
    else:
        result = pool.starmap_async(bm_write.output_account_statements,
                                    statement_parameters)
        result.wait()

# in serial mode, we fail immediately so no need to differentiate
if args.serial or result.successful():
    if not args.serial:
        result = result.get()
    logging.debug("Transactions written to files '{fi}'".format(fi=result))
    logging.info("Everything went well, "
                 "{cs} combined statement file(s) written".format(
                     cs=len(result)-result.count(None)))
    sys.exit(0)
else:
    logging.error("Something went wrong, check previous errors "
                  "and result '{rs}".format(rs=result.get()))
    sys.exit(1)
