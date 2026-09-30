"""Read only selected Parquet columns with HTTP range requests, excluding audio."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from qab.corpus import case_from_row, load_cases

CONFIG = json.loads((Path(__file__).parent / 'corpora.json').read_text())


def load_corpus(version, cases_dir=None):
    return load_bundle(version, cases_dir)[0]


def dataset_revision(version):
    """Commit sha of the dataset repo: one small API call, so a refresh can skip an unchanged parquet."""
    from huggingface_hub import HfApi
    return HfApi().dataset_info(CONFIG['versions'][version]['dataset']).sha


def load_bundle(version, cases_dir=None):
    if cases_dir:
        return load_cases(cases_dir=cases_dir), []
    spec = CONFIG['versions'][version]
    columns = ['id', 'duration_s', 'riwayah', 'reciter', 'description', 'style', 'content',
               'noisy', 'multi_surah', 'truth', 'segments', 'passages', 'recited_words', 'wpm', 'repeat_events']
    rows = read_rows(spec, columns)
    cases = [case_from_row(row) for row in rows]
    if not cases or len({c.id for c in cases}) != len(cases):
        raise ValueError('Corpus must be nonempty with unique recording ids')
    return cases, [{k: v for k, v in row.items() if k not in {'truth', 'segments'}} for row in rows]


def read_rows(spec, columns):
    import pyarrow.parquet as pq
    from huggingface_hub import HfFileSystem
    path = f"datasets/{spec['dataset']}/{spec['parquet']}"
    fs = HfFileSystem(skip_instance_cache=True)
    with fs.open(path, 'rb', block_size=65536) as f:
        metadata = pq.ParquetFile(f, pre_buffer=False).metadata

    def read_group(index):
        # Independent handles avoid cross-thread seek races. Never coalesce audio gaps.
        with fs.open(path, 'rb', block_size=65536) as f:
            return pq.ParquetFile(f, metadata=metadata, pre_buffer=False).read_row_group(
                index, columns=columns, use_threads=False).to_pylist()

    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = [row for group in pool.map(read_group, range(metadata.num_row_groups)) for row in group]
    return rows
