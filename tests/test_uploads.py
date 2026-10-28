"""POST /documents/upload: each format ingests, and bad input gets a 4xx, never a 500.

Runs the real app on a scratch database with embeddings faked (see conftest).
"""
import pytest

from tests.make_files import (
    DOCX_TYPES, make_blank_pdf, make_docx, make_encrypted_pdf, make_pdf, make_xlsx, make_zip,
)

MB = 1024 * 1024


@pytest.fixture
def upload(client, make_user, fake_embeddings):
    _, headers = make_user("alice")

    def post(filename: str, content: bytes):
        return client.post("/documents/upload", headers=headers, files={"file": (filename, content)})
    return post


def chunk_text_of(response) -> str:
    return "\n".join(c["chunk_text"] for c in response.json()["chunks"])


# ---- each format ingests ----

def test_txt(upload):
    r = upload("notes.txt", b"The lighthouse keeper was Ottoline Varga.")
    assert r.status_code == 200
    assert "Ottoline Varga" in chunk_text_of(r)


def test_csv_rows_are_labelled_with_sheet_and_header(upload):
    r = upload("people.csv", b"Name,Role\nOttoline,Keeper\nBram,Engineer\n")
    assert r.status_code == 200
    text = chunk_text_of(r)
    assert "people | Name: Ottoline; Role: Keeper" in text
    assert "people | Name: Bram; Role: Engineer" in text


def test_pdf(upload):
    r = upload("fact.pdf", make_pdf(["Ottoline Varga designed the tower", "It opened in 1887"]))
    assert r.status_code == 200
    assert "Ottoline Varga designed the tower" in chunk_text_of(r)
    assert "It opened in 1887" in chunk_text_of(r)


def test_docx_paragraphs_and_tables(upload):
    r = upload("fact.docx", make_docx(["Keeper's log"], table=[["Name", "Role"], ["Ottoline", "Keeper"]]))
    assert r.status_code == 200
    text = chunk_text_of(r)
    assert "Keeper's log" in text
    assert "Ottoline | Keeper" in text


def test_xlsx_rows_are_labelled_with_sheet_and_header(upload):
    r = upload("staff.xlsx", make_xlsx({"Staff": [["Name", "Role"], ["Ottoline", "Keeper"]],
                                        "Boats": [["Boat", "Size"], ["Gull", 12]]}))
    assert r.status_code == 200
    text = chunk_text_of(r)
    assert "Staff | Name: Ottoline; Role: Keeper" in text
    assert "Boats | Boat: Gull; Size: 12" in text


# ---- file types ----

def test_png_is_415(upload):
    r = upload("photo.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
    assert r.status_code == 415
    assert ".txt, .pdf, .docx, .xlsx or .csv" in r.json()["detail"]


@pytest.mark.parametrize("name, saved_as", [("old.doc", ".docx"), ("old.xls", ".xlsx")])
def test_legacy_office_formats_are_415(upload, name, saved_as):
    r = upload(name, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 50)
    assert r.status_code == 415
    assert saved_as in r.json()["detail"]


def test_no_extension_is_415(upload):
    assert upload("README", b"text").status_code == 415


@pytest.mark.parametrize("name, content", [
    ("fake.pdf", b"not a pdf at all"),
    ("fake.docx", b"plain text, not a zip"),
    ("fake.xlsx", make_zip({"[Content_Types].xml": DOCX_TYPES})),   # a docx renamed to .xlsx
    ("fake.docx", make_zip({"hello.txt": b"zip without [Content_Types].xml"})),
])
def test_content_must_match_extension(upload, name, content):
    assert upload(name, content).status_code == 415


# ---- text decoding and cleaning ----

def test_nul_bytes_are_dropped(upload):
    r = upload("nul.txt", b"Hello\x00 lighthouse\x00 keeper")
    assert r.status_code == 200
    assert "\x00" not in chunk_text_of(r)
    assert "Hello lighthouse keeper" in chunk_text_of(r)


def test_cp1252_csv(upload):
    r = upload("cities.csv", "Name,City\nJosé,Zürich\n".encode("cp1252"))
    assert r.status_code == 200
    assert "Name: José; City: Zürich" in chunk_text_of(r)


def test_utf8_bom_is_dropped(upload):
    r = upload("bom.txt", b"\xef\xbb\xbfFirst line")
    assert r.status_code == 200
    assert chunk_text_of(r) == "First line"


def test_whitespace_only_is_422(upload):
    r = upload("blank.txt", b"  \n\t \x00 \n")
    assert r.status_code == 422
    assert "No text" in r.json()["detail"]


# ---- unreadable files ----

def test_corrupt_pdf_is_422(upload):
    r = upload("broken.pdf", b"%PDF-1.4\nthis is not a real pdf body")
    assert r.status_code == 422
    assert r.json()["detail"] == "Could not read this file"


def test_encrypted_pdf_is_422(upload):
    r = upload("locked.pdf", make_encrypted_pdf())
    assert r.status_code == 422
    assert "password" in r.json()["detail"]


def test_pdf_without_text_is_422(upload):
    r = upload("scan.pdf", make_blank_pdf())
    assert r.status_code == 422
    assert "No text" in r.json()["detail"]


def test_corrupt_docx_with_valid_zip_is_422(upload):
    content = make_zip({"[Content_Types].xml": DOCX_TYPES, "word/document.xml": b"<not-xml"})
    r = upload("broken.docx", content)
    assert r.status_code == 422
    assert r.json()["detail"] == "Could not read this file"


# ---- limits ----

def test_pdf_over_100_pages_is_413(upload):
    r = upload("long.pdf", make_blank_pdf(pages=101))
    assert r.status_code == 413
    assert "pages" in r.json()["detail"]


def test_xlsx_over_20_sheets_is_413(upload):
    r = upload("many.xlsx", make_xlsx({f"S{i}": [["A"], ["x"]] for i in range(21)}))
    assert r.status_code == 413
    assert "sheets" in r.json()["detail"]


def test_xlsx_over_2000_rows_is_413(upload):
    r = upload("tall.xlsx", make_xlsx({"Big": [["N"]] + [[i] for i in range(2000)]}))   # 2001 rows with the header
    assert r.status_code == 413
    assert "rows" in r.json()["detail"]


def test_csv_over_2000_rows_is_413(upload):
    r = upload("tall.csv", ("N\n" + "\n".join(str(i) for i in range(2000)) + "\n").encode())
    assert r.status_code == 413
    assert "rows" in r.json()["detail"]


def test_file_over_5mb_is_413(upload):
    r = upload("big.txt", b"a" * (5 * MB + 1))
    assert r.status_code == 413
    assert "5 MB" in r.json()["detail"]


def test_file_of_exactly_5mb_passes_the_size_check(upload):
    # Rejected later for too much text, not for size
    r = upload("big.txt", b"a" * (5 * MB))
    assert r.status_code == 413
    assert "too much text" in r.json()["detail"]


def test_text_over_300k_chars_is_413(upload, fake_embeddings):
    r = upload("long.txt", b"word " * 70_000)
    assert r.status_code == 413
    assert "too much text" in r.json()["detail"]
    fake_embeddings.assert_not_called()


def test_over_300_chunks_is_413_and_nothing_is_embedded(upload, fake_embeddings):
    text = " ".join(f"This is sentence number {i} of a long document." for i in range(5000)).encode()
    assert len(text) < 300_000          # so it is the chunk limit that trips, not the text limit
    r = upload("long.txt", text)
    assert r.status_code == 413
    assert "chunks" in r.json()["detail"]
    fake_embeddings.assert_not_called()


def test_zip_bomb_is_413(upload):
    # ~60 MB unpacked, about 60 KB on the wire (zeros deflate ~1000:1)
    bomb = make_zip({"[Content_Types].xml": DOCX_TYPES, "word/bomb.bin": b"\x00" * (60 * MB)})
    assert len(bomb) < 5 * MB
    r = upload("bomb.docx", bomb)
    assert r.status_code == 413
    assert "unpacked" in r.json()["detail"]


def test_zip_with_too_many_entries_is_413(upload):
    members = {"[Content_Types].xml": DOCX_TYPES}
    members.update({f"word/{i}.xml": b"x" for i in range(1000)})       # 1001 entries
    r = upload("many.docx", make_zip(members))
    assert r.status_code == 413


# ---- filenames ----

def test_300_char_filename_is_stored_truncated_with_its_extension(upload):
    r = upload("a" * 296 + ".txt", b"Some text")
    assert r.status_code == 200
    assert len(r.json()["filename"]) == 255
    assert r.json()["filename"].endswith(".txt")


@pytest.mark.parametrize("sent, stored", [
    ("C:\\Users\\bob\\notes.txt", "notes.txt"),
    ("../../etc/notes.txt", "notes.txt"),
])
def test_filename_is_reduced_to_a_clean_basename(upload, sent, stored):
    r = upload(sent, b"Some text")
    assert r.status_code == 200
    assert r.json()["filename"] == stored


def test_clean_filename_drops_nul_and_surrogates_and_defaults():
    # Unit-level: the HTTP client percent-encodes a raw NUL, so it can't be sent through the route
    from app.extract import clean_filename
    assert clean_filename("bad\x00na\ud800me.txt") == "badname.txt"
    assert clean_filename("") == "upload"
    assert clean_filename(None) == "upload"
    assert clean_filename("dir/") == "upload"


def test_failed_upload_leaves_no_document(client, make_user, fake_embeddings):
    _, headers = make_user("alice")
    client.post("/documents/upload", headers=headers, files={"file": ("x.pdf", b"%PDF-1.4 junk")})
    assert client.get("/documents/", headers=headers).json() == []
