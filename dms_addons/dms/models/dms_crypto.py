"""Password protection for DMS files, using standard libraries.

* Server copy: every file is kept inside a password-protected ZIP (WinZip AES-256, opened by
  7-Zip, WinRAR, Windows and most unzip tools), so a copy taken straight from the server
  needs the server password.
* Download copy: a separate download password. PDF and Office Open XML files are protected
  in their own format (they open in their usual app with a password prompt); every other
  type goes into an AES-256 ZIP.
"""
import io
import logging
import os

import pyzipper

_logger = logging.getLogger(__name__)

OOXML_EXT = ('docx', 'xlsx', 'xlsm', 'pptx', 'dotx', 'xltx', 'ppsx')
ZIP_SUFFIX = '.zip'


def zip_encrypt(data, name, password):
    buf = io.BytesIO()
    with pyzipper.AESZipFile(buf, 'w', compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as z:
        z.setpassword(password.encode('utf-8'))
        z.setencryption(pyzipper.WZ_AES, nbits=256)
        z.writestr(name or 'file', data)
    return buf.getvalue()


def zip_decrypt(blob, password):
    """Bytes of the single file inside; raises RuntimeError on a wrong password."""
    with pyzipper.AESZipFile(io.BytesIO(blob)) as z:
        z.setpassword(password.encode('utf-8'))
        names = z.namelist()
        if not names:
            raise ValueError('empty archive')
        return z.read(names[0])


def _protect_pdf(data, password):
    from PyPDF2 import PdfReader, PdfWriter
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        return None
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt(password)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _protect_ooxml(data, password):
    if not data.startswith(b'PK'):
        return None  # already encrypted by its author
    from msoffcrypto.format.ooxml import OOXMLFile
    out = io.BytesIO()
    OOXMLFile(io.BytesIO(data)).encrypt(password, out)
    return out.getvalue()


def protect_for_download(data, filename, password):
    """(bytes, filename) of a password-protected copy for download."""
    ext = os.path.splitext(filename or '')[1].lstrip('.').lower()
    try:
        if ext == 'pdf':
            done = _protect_pdf(data, password)
            if done:
                return done, filename
        elif ext in OOXML_EXT:
            done = _protect_ooxml(data, password)
            if done:
                return done, filename
    except Exception as e:  # noqa: BLE001 - fall back to the ZIP, which works for any file
        _logger.info("DMS: native protection failed for %s, using ZIP: %s", filename, e)
    return zip_encrypt(data, filename, password), (filename or 'file') + ZIP_SUFFIX
