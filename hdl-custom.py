#!/usr/bin/env python3
#
# A basic command line interface for some custom administration for Handle.Net a.k.a. EPIC Persistent Identifiers.

import argparse
import csv
import json
import logging
import os
import ssl
import textwrap
import urllib.request
from datetime import date
from time import monotonic, sleep


def parse_args():
    """
    Parse and validate the command line arguments, and set the defaults.
    """
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawTextHelpFormatter,
        description='Utility commands for handle.net EPIC persistent identifiers')
    parser.add_argument('command',
                        metavar='COMMAND',
                        choices=['handle', 'handles', 'count', 'download', 'rewrite-aliases'],
                        help=textwrap.dedent('''\
          command to run:
          - `handle`: retrieve details for the given POSTFIX; this may be the same output as the
             public endpoint `https://hdl.handle.net/api/handles/<prefix>/<postfix>?pretty`
          - `handles`: retrieve details for the given postfixes taken from a file
          - `count`: count existing handles on the server, including special postfixes such as `ADMIN`, `CONTACT`,
            `EPIC_HEALTHCHECK` and `USER01`
          - `download`: create file with existing handles, each line holding `1-based-counter; prefix/postfix`
          - `rewrite-aliases`: rewrite handles based on a file, each line holding `postfix; postfix` where both
             should already exist as a handle, and where the first will become an alias for the latter
          '''))
    parser.add_argument('postfix', metavar='POSTFIX', nargs='?',
                        help='optional postfix, for a single full handle `<prefix>/<postfix>`')
    parser.add_argument('-p', '--prefix', required=True, help='prefix, like `21.12102`, required')
    parser.add_argument('-i', '--index', required=True, help='user index, like `312`, required')
    parser.add_argument('--server', default='https://epic-pid.storage.surfsara.nl:8001',
                        help='base PID server URL, default `https://epic-pid.storage.surfsara.nl:8001`, to which, '
                             'e.g., `/api/sessions` and `/api/handles` are appended')
    parser.add_argument('--certfile', help='certificate file, default `<prefix>_USER01_<index>_certificate_only.pem`')
    parser.add_argument('--keyfile', help='private key file, default `<prefix>_USER01_<index>_privkey.pem`')
    parser.add_argument('-f', '--file', metavar='INPUT', help='semicolon-separated input file, default `<command>.csv`')
    parser.add_argument('-o', '--output', help='semicolon-separated output file, default `<command>-<yyyymmdd>.csv`')
    parser.add_argument('--start', type=int,
                        help='zero-based start row from input file (default 1, hence ignoring the header), or start '
                             'page when downloading handles (default 0)')
    parser.add_argument('--count', default=3, type=int,
                        help='number of rows to process or pages to download, default 3')
    parser.add_argument('--size', metavar='PAGESIZE', default=10000, type=int,
                        help='page size when downloading paginated data, default 10,000')
    parser.add_argument('--throttle', metavar='SECONDS', default=10, type=float,
                        help='number of seconds between requests, default 10')
    parser.add_argument('-l', '--log', help='log file, default `<command>-<yyyymmdd>.log`')
    parser.add_argument('-q', '--quiet', help='reduce output on terminal to be the same as the log',
                        action='store_true')
    args = parser.parse_args()

    args.certfile = args.certfile or f'{args.prefix}_USER01_{args.index}_certificate_only.pem'
    args.keyfile = args.keyfile or f'{args.prefix}_USER01_{args.index}_privkey.pem'
    args.file = args.file or f'{args.command}.csv'
    args.output = args.output or f'{args.command}-{date.today().strftime("%Y%m%d")}.csv'
    args.log = args.log or f'{args.command}-{date.today().strftime("%Y%m%d")}.log'

    # For `rewrite-aliases` default to 1, skipping the CSV header
    args.start = args.start if args.start is not None else 1 if args.command == 'rewrite-aliases' else 0

    return args


def setup_logger(args):
    logger = logging.getLogger()
    logger.setLevel(logging.INFO if args.quiet else logging.DEBUG)

    fmt = os.environ.get('LOG_FORMAT', '%(asctime)s.%(msecs)03d; %(levelname)s; %(message)s')
    datefmt = os.environ.get('LOG_DATEFORMAT', '%Y-%m-%d %H:%M:%S')
    formatter = logging.Formatter(fmt, datefmt)

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG)
    console.setFormatter(formatter)
    logger.addHandler(console)

    file = logging.FileHandler(args.log)
    file.setLevel(logging.INFO)
    file.setFormatter(formatter)
    logger.addHandler(file)
    logging.debug(f'Writing INFO logging to `{args.log}`')


def start_session(args: {}):
    """
    See also '14.7 Sessions API' in http://hdl.handle.net/20.1000/113
    """
    if not os.path.isfile(args.keyfile):
        logging.error(f'Failed to find private key `{args.keyfile}`')
        exit(1)
    logging.debug(f'Using private key `{args.keyfile}`')

    if not os.path.isfile(args.certfile):
        logging.error(f'Failed to find certificate `{args.certfile}`')
        exit(1)
    logging.debug(f'Using certificate `{args.certfile}`')

    logging.debug('Creating new session')
    req = urllib.request.Request(url=f'{args.server}/api/sessions', method='POST')
    with urllib.request.urlopen(req) as f:
        # Expecting 200 OK with something like:
        #   {
        #     "sessionId": "node0n78evxqet4lqfap5frin2dbj4141",
        #     "nonce": "Df1HlFib4vc8CcWZqHuKXQ=="
        #   }
        session = json.load(f)
        logging.debug(f'Got {f.status} {f.reason}: {session}')

    session_id = session['sessionId']

    logging.debug(f'Authorizing sessionId {session_id}')
    context = ssl.create_default_context()
    context.load_cert_chain(certfile=args.certfile, keyfile=args.keyfile)
    headers = {'Authorization': f'Handle clientCert="true", sessionId="{session_id}"'}
    req = urllib.request.Request(url=f'{args.server}/api/sessions/this', method='PUT', headers=headers)
    with urllib.request.urlopen(req, context=context) as f:
        # Expecting 200 OK with something like:
        #   {
        #     "sessionId": "node0n78evxqet4lqfap5frin2dbj4141",
        #     "nonce": "Df1HlFib4vc8CcWZqHuKXQ==",
        #     "authenticated": true,
        #     "id": "312:21.12102/USER01"
        #   }
        # Make sure to log this to file, just in case this script fails and we want to delete the session
        logging.info(f'Got authorized session; {json.load(f)}')

    args.session_id = session_id


def delete_session(args):
    headers = {'Authorization': f'Handle sessionId="{args.session_id}"'}
    logging.debug(f'Deleting sessionId {args.session_id}')
    req = urllib.request.Request(url=f'{args.server}/api/sessions/this', method='DELETE', headers=headers)
    with urllib.request.urlopen(req) as f:
        # Expecting 204 No Content
        logging.debug(f'Got {f.status} {f.reason}')


def get_page_of_handles(args, page: int, page_size: int):
    """
    Get a page of existing handles, without any detail about their types or target URLs.

    According to https://servicedesk.surfsara.nl/wiki/display/WIKI/Handle+HTTP+JSON+REST+API+using+bash

      Note: Please do NOT list more than 10.000 handles at a time. Otherwise the handle server will be overflowed!

    If the page size is zero a count of handles is returned, but no handles. Forgetting either ``page`` or ``pageSize``
    (or using different letter casing, or a negative value) is interpreted as a request for all handles, likely throwing
    a timeout or even a 500 Internal Server Error for large sets.

    February 2021: getting a batch of 10 or 10,000 handles (for a prefix that holds 13M+ handles) takes almost 30
    seconds, regardless the batch size. The standard ``hdl-admintool`` software gets **the full list** in a few minutes.
    """
    logging.debug(f'Getting handles; page={page}; size={page_size}')

    headers = {'Authorization': f'Handle sessionId="{args.session_id}"'}
    req = urllib.request.Request(url=f'{args.server}/api/handles?prefix={args.prefix}&page={page}&pageSize={page_size}',
                                 method='GET', headers=headers)
    start_time = monotonic()
    with urllib.request.urlopen(req) as f:
        # Expecting 200 OK with something like:
        #   {
        #     "responseCode": 1,
        #     "prefix": "21.12102",
        #     "totalCount": "13230846",
        #     "page": 0,
        #     "pageSize": 10,
        #     "handles": [
        #       "21.12102/000000568BF64872B166F6A9D906486A",
        #       "21.12102/00000135D00847B98D7404EA1B01EE3E",
        #       ...
        #       "21.12102/00000A50CB2046E3889F58263926F616"
        #     ]
        #   }
        result = json.load(f)
    end_time = monotonic()
    elapsed = end_time - start_time
    logging.debug(f'Got handles; time={elapsed}')
    return result


def download_handles(args):
    logging.debug(f'Writing results to `{args.output}`')
    stop = args.start + args.count

    with open(args.output, 'a') as output:
        for page in range(args.start, stop):
            # One-based
            first = page * args.size + 1
            result = get_page_of_handles(args, page, args.size)
            for idx, handle in enumerate(result['handles']):
                counter = first + idx
                # One-based counter
                output.write(f'{counter};{handle}\n')
            output.flush()

            result_size = len(result['handles'])
            last = first + result_size - 1
            logging.info(f'Got handles; page={page}; size={result_size}; first={first}; last={last}')

            # Simply using ``result_size < page_size`` may yield an additional last request with zero results, but of
            # course for a default page size of 10,000 those chances are low
            if last >= int(result['totalCount']):
                logging.debug('No more results')
                break

            if page < stop - 1:
                logging.debug(f'Throttling; sleep={args.throttle}sec')
                sleep(args.throttle)

    logging.debug(f'Done; start page={args.start}; next page={stop}; page size={args.size}; output={args.output}')


def count_handles(args):
    stats = get_page_of_handles(args, 0, 0)
    logging.info(f'prefix={stats["prefix"]}; count={stats["totalCount"]}')


def get_handle(args, postfix: str):
    """
    Get a single handle. Using ``https://hdl.handle.net/api/handles/<prefix>/<postfix>?pretty`` may be easier.
    """
    logging.debug(f'Getting handle; prefix={args.prefix}; postfix={postfix}')
    headers = {'Authorization': f'Handle sessionId="{args.session_id}"'}
    req = urllib.request.Request(url=f'{args.server}/api/handles/{args.prefix}/{postfix}', method='GET',
                                 headers=headers)
    with urllib.request.urlopen(req) as f:
        # We could get an object directly by using `result = json.load(f)`, but logging pure JSON (with double quotes)
        # may help for future parsing of the logs
        response = f.read().decode('utf-8')
    logging.info(f'Got handle; handle={response}')
    return json.loads(response)


def get_handles(args):
    """
    Get the full handles based on the postfix in the first column of the CSV input file, and write the results to the
    CSV output file.
    """
    logging.debug(f'Loading data from `{args.file}`')
    logging.debug(f'Writing results to `{args.output}`')
    stop = args.start + args.count

    with open(args.file) as csv_file, open(args.output, 'a') as output:
        reader = csv.reader(csv_file, delimiter=';', quotechar='"')
        line = 0
        for [postfix, *_] in reader:
            if line >= args.start:
                logging.debug(f'line={line}; postfix={postfix}')
                handle = get_handle(args, postfix)
                # Zero-based counter, but the first (header) line may be skipped
                output.write(f'{line};{handle}\n')
                output.flush()
                if line < stop - 1:
                    logging.debug(f'Throttling; sleep={args.throttle}sec')
                    sleep(args.throttle)

            line = line + 1
            if line >= stop:
                break

    logging.debug(f'Done; start={args.start}; next={stop}; output={args.output}; log={args.log}')


def rewrite_to_alias(args, postfix: str, target_postfix: str):
    """
    Convert the existing handle identified by ``postfix`` to become an ``HS_ALIAS`` to the existing ``target_postfix``.
    """
    logging.info(f'Rewriting handle to become alias; prefix={args.prefix}; alias={postfix}; target={target_postfix}')
    headers = {'Authorization': f'Handle sessionId="{args.session_id}"', 'Content-Type': 'application/json'}
    data = {
        'values': [
            {'index': 1,
             'type': 'HS_ALIAS',
             'data': {
                 'format': 'string',
                 'value': f'{args.prefix}/{target_postfix}'
             }}
        ]
    }
    req = urllib.request.Request(url=f'{args.server}/api/handles/{args.prefix}/{postfix}?index=various', method='PUT',
                                 headers=headers, data=json.dumps(data).encode('utf-8'))

    with urllib.request.urlopen(req) as f:
        # Expecting 200 OK with something like:
        #   {
        #     "responseCode": 1,
        #     "handle": "21.12102/cc724d36-24b6-4df8-a8e0-f04fac555063"
        #   }
        # This will throw 404 Not Found if the handle does not exist yet
        response = f.read().decode('utf-8')
    logging.debug(f'Rewrote handle; url=https://hdl.handle.net/{args.prefix}/{postfix}; response={response}')


def rewrite_aliases(args):
    logging.debug(f'Loading data from `{args.file}`')
    line = 0
    stop = args.start + args.count

    with open(args.file) as csv_file:
        reader = csv.reader(csv_file, delimiter=';', quotechar='"')
        for [alias_postfix, target_postfix, *_] in reader:
            if line >= args.start:
                logging.info(f'line={line}; alias={alias_postfix}; handle={target_postfix}')
                rewrite_to_alias(args, alias_postfix, target_postfix)
                if line < stop - 1:
                    logging.debug(f'Throttling; sleep={args.throttle}sec')
                    sleep(args.throttle)

            line = line + 1
            if line >= stop:
                break

        else:
            # Ensure we can tell we've reached EOF even if args.count nicely matches the number of processed lines
            stop = -1

    logging.info(f'Done; start={args.start}; last={line - 1}; next={stop if stop > -1 else "n/a"}; log={args.log}')


def run():
    args = parse_args()
    setup_logger(args)
    start_session(args)

    try:
        if args.command == 'count':
            count_handles(args)
        elif args.command == 'handle':
            get_handle(args, postfix=args.postfix)
        elif args.command == 'handles':
            get_handles(args)
        elif args.command == 'download':
            download_handles(args)
        elif args.command == 'rewrite-aliases':
            rewrite_aliases(args)
        else:
            logging.error(f'Unsupported command; command={args.command}')
    except KeyboardInterrupt:
        logging.info('Interrupted by user')
        exit(130)
    except Exception as e:
        logging.error(e)
        raise e
    finally:
        delete_session(args)


if __name__ == '__main__':
    run()
