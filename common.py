import json
import logging
import zipfile

from functools import lru_cache
from subprocess import check_output

def clean_num(text):
    return round(float(text.replace(' ', '').replace(',', '.')))

def clean_hours(text):
    return int(text.split(':')[0])

def pretty(obj):
    print(json.dumps(obj, sort_keys=True, indent=4))

def setup_logging(level=logging.WARNING):
    logging.basicConfig(
        level=level,
        format='%(levelname)s: %(message)s')

def tabulate_poor(tabular_data, headers=(), **kwargs):
    toprint = []
    if headers:
        toprint.append(' \t'.join(headers))
        toprint.append('---')

    for row in tabular_data:
        toprint.append(' \t'.join(map(str, row)))

    return '\n'.join(toprint)

#@lru_cache()
def load_pdf_file(filename):
    logging.debug('Loading pdf..')
    command_args = ['pdftotext', filename, '-']
    logging.debug(' '.join(command_args))
    output = check_output(command_args)
    logging.debug('done.')
    return output.decode('utf-8')
    # with open(filename, 'rb') as f:
    #     return f.read()

def extract_pdf_from_zip(filename):
    logging.debug('Extracting zip..')
    zf = zipfile.ZipFile(filename, 'r')

    for pdffile in zf.filelist:
        if 'ENG' in pdffile.filename:
            break
    else:
        raise Exception("Didn't find an english pdf file in the zip archive")

    zippasswd = open('.zippasswd', 'rb').read()
    zf.extract(pdffile.filename, pwd=zippasswd)
    logging.debug('done.')
    return pdffile.filename
