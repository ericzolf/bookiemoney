#!/bin/sh -x
# if you place this script in a directory named like a flavour and containing
# all the statement files pertaining to this bank, you can rename them by
# simply calling the script (of course you need to adapt the location of
# bookiemoney)
# Note that you can just link this script with something like
# 	ln -s .../00rename.sh .../${flavour}/

CURRDIR=$(realpath $(dirname $0) )
FLAVOUR=$(basename "${CURRDIR}" | tr A-Z a-z)

if [ -d ~/Comp/bookiemoney ]
then
	cd ~/Comp/bookiemoney
elif [ -d ~/Public/bookiemoney ]
then
	cd ~/Comp/bookiemoney
fi
./rename_bank_statements.py --flavour ${FLAVOUR} "$@" "${CURRDIR}/*"
