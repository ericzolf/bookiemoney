"""
Bookiemoney module to clean an account statement and its transactions
before writing it down.
"""

import babel.numbers as babelnum
import babel.dates as babeldate
import locale
import logging
import os

# Babel doesn't offer the function to get the 3 letters code from a
# currency symbol, so we need to do it ourselves
CURRENCY_MAP = {babelnum.get_currency_symbol(x): x
                for x in babelnum.list_currencies()
                if x != babelnum.get_currency_symbol(x)}


def clean_account_statement(account_statement):
    """
    Clean an account statement and its transactions according to fixed rules

    The main purpose is to make sure that the records fulfil minimal expected
    requirements:
    * values like currency, amounts and currency are clean and standardized
      (see clean_value function)
    * each transaction has a date, the account's UID, a counterpart name,
      an amount, and a balance amount, both with currency

    Returns the cleaned statement
    """

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
        logging.debug(
            "Cleaning up transaction '{tr}' for account '{ac}'".format(
                tr=line, ac=account_uid))
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
        # booking and value dates. We need a value which doesn't jump and
        # it is the booking date because the value date might be in the past.
        if 'transaction_date' not in line:
            if 'transaction_booking_date' in line:
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
    logging.debug("Cleaning {ke}={va}".format(ke=key, va=value))
    if key.endswith('_amount'):
        return babelnum.parse_decimal(value, locale=cfg['locale'])
    elif key.endswith('_quantity'):
        return int(value)
    elif key.endswith('_currency'):
        return CURRENCY_MAP.get(value, value)
    elif key.endswith('_date'):
        return babeldate.parse_date(value, locale=cfg['locale'])
    elif key.endswith('_payment_type') and 'payment_types' in cfg:
        return cfg['payment_types'].get(value, value)

    return value


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
