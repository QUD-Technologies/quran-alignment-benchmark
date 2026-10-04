"""Stage and deploy the reviewed leaderboard source; never upload local drafts."""
import os
import secrets
import shutil
import tempfile
from pathlib import Path

from huggingface_hub import HfApi, bucket_info, get_token


def main():
    root = Path(__file__).resolve().parents[1]
    space = 'QUD-Technologies/quran-alignment-leaderboard'
    bucket = 'QUD-Technologies/quran-alignment-leaderboard'
    api = HfApi()
    if not bucket_info(bucket).private:
        raise RuntimeError('Submission bucket must be private')
    api.create_repo(space, repo_type='space', space_sdk='docker', exist_ok=True)
    api.add_space_variable(space, 'QAB_BUCKET', bucket)
    api.add_space_secret(space, 'HF_TOKEN', get_token())
    # Set once in deployment environments to preserve active logins on subsequent deployments.
    if os.getenv('QAB_NEW_SESSION_SECRET') == '1':
        api.add_space_secret(space, 'SESSION_SECRET', secrets.token_hex(32))
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp)
        for name in ['src', 'leaderboard']:
            shutil.copytree(root / name, target / name,
                            ignore=shutil.ignore_patterns('node_modules', 'dist', '__pycache__', '.pytest_cache'))
        for name in ['pyproject.toml', 'README.md', 'LICENSE']:
            shutil.copy2(root / name, target / name)
        shutil.copy2(root / 'leaderboard/Dockerfile', target / 'Dockerfile')
        shutil.copy2(root / 'leaderboard/SPACE.md', target / 'README.md')
        result = api.upload_folder(repo_id=space, repo_type='space', folder_path=tmp,
                                   commit_message='Build Quran alignment leaderboard and private submissions')
        print(result.oid)


if __name__ == '__main__':
    main()
