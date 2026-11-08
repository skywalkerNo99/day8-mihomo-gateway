"""Extract mainland CN ranges from the verified DB-IP Country Lite CSV release."""
import argparse
import csv
import gzip
import hashlib
import io
import ipaddress
from pathlib import Path


def extract(source, checksum):
    with gzip.open(source, 'rb') as stream:
        data = stream.read()
    if hashlib.sha1(data).hexdigest() != checksum.lower():
        raise ValueError('Uncompressed CSV does not match the DB-IP release checksum')
    groups = {4: [], 6: []}
    for first, last, country in csv.reader(io.StringIO(data.decode('ascii'))):
        if country == 'CN':
            start, end = map(ipaddress.ip_address, (first, last))
            groups[start.version].extend(ipaddress.summarize_address_range(start, end))
    if not all(groups.values()):
        raise ValueError('Missing IPv4 or IPv6 country ranges')
    return [str(net) for nets in groups.values() for net in ipaddress.collapse_addresses(nets)]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv_gz', type=Path)
    parser.add_argument('csv_sha1', help='SHA1SUM of the CSV published on the DB-IP download page')
    args = parser.parse_args()
    ranges = extract(args.csv_gz, args.csv_sha1)
    target = Path(__file__).resolve().parents[1] / 'rules/cn-networks.txt'
    target.write_text('\n'.join(ranges) + '\n', encoding='ascii')
    print(f'{len(ranges)} networks')
