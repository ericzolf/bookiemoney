"""
Bookiemoney module to write statements and their transactions to a CSV file
"""

import csv
import logging
import os
import yaml

# maximum possible number of transactions each day
DAILY_TRANSACTIONS = 10000


def output_account_statements(statements, out_file, flavour, plug_gaps):
    """
    Combine all transactions of multiple statements and write them to a file
    """
    transactions = combine_account_statements(statements)
    return output_transactions(transactions, out_file, flavour, plug_gaps)


def combine_account_statements(statement):
    """
    Combine the transactions of multiple statements from the same account

    Return a sorted dictionary of transactions where the key is the unique ID
    of each transaction
    """
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
    Callable object returns a unique ID within a given account taking a date

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


def output_transactions(transactions, out_file, flavour, plug_gaps):
    """
    Write all transactions into the output file according to flavour.

    Plug gaps between transaction balance values if plug gaps is True.

    The function could read first the output file and combine it with the
    new transactions, plugging the gaps afterwards, but this possibility
    isn't implemented yet.
    Currently the output file is overwritten each time.
    """

    if not transactions.values():
        logging.warning("No transactions to write to output file")
        return None  # nothing to write in a file...

    # an output file can have a placeholder for the account unique ID
    if "{}" in out_file:
        # all transactions must have the same account UID so we don't care
        # and take the first one
        out_file = out_file.format(
            next(iter(transactions.values()))['transaction_account_uid'])

    # the extension of the file gives us the file type
    extension = os.path.splitext(out_file)[1].lstrip('.').lower()

    # we read the flavour's configuration file
    flavour_file = os.path.join('out', extension, flavour + '.yml')
    with open(flavour_file, mode='r') as yfd:
        flavour_config = yaml.safe_load(yfd)

    # normalize the valour fields to allow for shortcuts
    normalize_flavour_fields(flavour_config['fields'])
    # get the list of output fields
    fields = list(flavour_config['fields'].keys())

    if plug_gaps:
        transactions = plug_gaps_in_statement(transactions)

    logging.info("Writing {tr} transactions to output file '{of}'".format(
        tr=len(transactions), of=out_file))
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
        for uid in transactions:
            row = {}
            for field in fields:
                value = get_field_value(field, transactions[uid],
                                        flavour_config['fields'][field],
                                        flavour_config.get('locale'))
                row[field] = value
            logging.debug(row)
            writer.writerow(row)
    return out_file


def normalize_flavour_fields(fields):
    """
    Normalize the list of fields in a flavour to allow for "shortcuts".

    None fields are mapped to themself i.e. '$key'
    And single field values are put into a list
    Such shortcuts make the field empty if nothing matches (without error)
    """
    for key in fields:
        if fields[key] is None:
            fields[key] = {'value': ['$' + key, '']}
        if not isinstance(fields[key]['value'], list):
            fields[key]['value'] = (fields[key]['value'], '')


def get_field_value(field, transaction, field_map, out_locale=None):
    """
    Apply the field_map to transaction to extract the expected field

    Returns the extracted field value or a KeyError exception
    Note that the field parameter is only required for better error message
    """
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


def plug_gaps_in_statement(statement):
    """
    If two successive transactions in a statement present a gap in the
    account's balance, add a "gap" transaction to plug it.

    It is assumed that the given statement is a dictionary already sorted by
    transaction order.
    The original dictionary of transactions is returned with the found gap
    transactions added, and sorted again by transaction uid.
    Note that there will always be a gap transaction if the initial balance
    before the statement isn't zero.
    """
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
