# Corpus notes (v1)

What the dataset rows contain, how the ground truth was produced, and what v1 does not cover. The dataset lives at [`hetchyy/quran-alignment-benchmark`](https://huggingface.co/datasets/hetchyy/quran-alignment-benchmark); one config per corpus version (`v1`), one split (`test`). A config is immutable once a score has been published against it; a re-annotation or an added recording is the next config.

## Columns

| column | type | filled by | meaning |
|---|---|---|---|
| `id` | string | declared | stable, descriptive; `<who>-<what>` (section below) |
| `audio` | Audio | bucket | the reviewed file, byte-for-byte; CBR MP3 |
| `riwayah` | string | declared | reading whose numbering the truth uses; `hafs_an_asim` for every v1 row |
| `description` | string, nullable | declared | what the audio contains beyond what the other columns say (capture conditions, what is cut, how it is noisy); null when self-describing |
| `reciter` | string | declared | display name |
| `style` | `murattal` / `mujawwad` / `hadr` / `muallim` | declared | the consumer's word; `wpm` is the measurement behind it. `muallim` is a teaching recording: the reciter reads a phrase and a class repeats it |
| `content` | `quran_only` / `prayer` | declared | either only Quran, or a prayer capture carrying athan, dua, transitions and congregation. New values (`khutbah`, `lecture`) are added to the enum with a scorer release |
| `noisy` | bool | declared | `true` = degraded capture (limiter-crushed, reverberant, audible background noise); `false` = studio, clean master, or a clean PA feed |
| `passages` | string | derived | chapter names, with a verse range when partial; al-Fatiha omitted when other content exists; the two juz' recordings say `Juz' Amma` / `Juz' Tabarak` |
| `multi_surah` | bool | derived | more than one chapter in the truth (Fatiha counts) |
| `duration_s` | float | derived | full audio duration in seconds |
| `recited_words` | int | derived | Quran word instances in the truth; a repeated word counts every time it is recited |
| `wpm` | int | derived | `recited_words` over the minutes of Quran-bearing segments, not over the full duration (the prayer captures read 11-32 wpm over total time, which is silence and dua, and 42-44 over Quran time, their real pace) |
| `repeat_events` | int | derived | backward jumps in reading order (SPEC 4.5) |
| `truth` | struct | derived | `words[{word, start_s, end_s}]` and `non_quran[{start_s, end_s}]`; the scorer's input (SPEC 2) |
| `segments` | list of struct | derived | the reviewed segmentation: `{start_s, end_s, first_word, last_word}` per Quran or formula segment, edges taken from the first and last word times in `truth` |

Derived columns are computed from `truth` by `tools/export_corpus.py` and never hand-edited; `wpm` uses the `segments` durations. Word numbering (`S:A:W`) is the whitespace tokenisation of the QUL QPC Hafs text (`text_qpc_hafs`, v3.12): 6236 verses, 77,433 words; the per-verse counts are bundled in the scorer as `qab/data/hafs.json`.

### Style versus words per minute

`wpm` over Quran time separates murattal, mujawwad and hadr without overlap. Both columns stay: `style` is what a reader filters on and a contributor declares; `wpm` shows that Diban at 170 and Afasy at 78 are both hadr but not the same task, and that `muallim` is a structure (every phrase twice, one side of it children) rather than a pace — its 58 wpm sits in the murattal band.

| style | recordings | min | words | wpm range |
|---|---|---|---|---|
| murattal | 9 | 165 | 6151 | 41-58 |
| mujawwad | 2 | 63 | 1453 | 28-30 |
| hadr | 4 | 110 | 9699 | 78-170 |
| muallim | 1 | 19 | 1118 | 58 |

## Ids

`<who>-<what>`. `who` is the reciter's common surname, or the setting for a prayer capture (`prayer`, `witr`, `taraweeh`), since that row is about the capture, not the imam. `what` is the chapter, chapters, or juz' in short English. Lower-case, hyphenated, no numbers, no style or version suffix. An id is never reused: a second Fatir by Luhaidan would be `luhaidan-fatir-2`; a re-annotation of the same audio keeps the id and lands in the next config.

## How the ground truth was made

Every recording was segmented and reviewed by ear in the Inspector (the maintainers' annotation tool): segment edges, the reference of every segment, repeats as their own segments, and non-Quran regions. Word times inside each reviewed segment start from forced alignment (Montreal Forced Aligner with the maintainers' Quran acoustic models, beam 400) run per segment on its reviewed text. They are then checked automatically (word count against the reference, ordering, length-normalised sliver words, and the gap between each edge word and its segment edge: a first word starting seconds into a segment is a word the aligner gave to silence) and reviewed manually by ear in the Inspector, where wrong boundaries were corrected. Segments the primary model refused, and the three Luhaidan captures where it lost edge words, were re-timed with a second model before that review.

The exporter then pads word times the way a karaoke display pads them: the first word starts at the segment start, each word's end moves to the next word's start (no gaps inside a segment), and the last word ends at the segment end. Two exceptions, declared per recording in the manifest (`pad_tail: false`): the mujawwad rows (`abdulbasit-raad`, `husary-fath`) keep the raw end of the last word, because their trailing madd is followed by a segmenter pad that is not speech. Where two reviewed segments overlap by a few tens of milliseconds, the earlier word ends where the next begins. Word boundaries inside a segment are reviewed, but the edge between two words in connected recitation is not a sharp point; treat them as approximate to the order of 50-100 ms.

`segments` therefore equals the reviewed segment edges everywhere except the mujawwad tails.

One recording is cut: `diban-sad` is an excerpt whose source file runs 0.9 s past the last word into the opening of the next track, so its audio is truncated at the frame boundary after the recitation ends (`trim_end_s` in the manifest, a whole-frame MP3 cut with no re-encode) and its last word ends at the new end of the audio.

Formula words are labelled `Basmala:k` / `Isti'adha:k`. In the prayer captures the imams recite the Basmala silently, so every Fatiha starts at `1:2:1`.

## Coverage and gaps

v1 is a benchmark of professional recitation, congregational prayer and teaching in Hafs: four styles, single and multi-chapter, clean and degraded captures, dense repeats (Fatir: 27 events in 18 minutes; Luqman: every one of its 70 phrases repeated), whole-verse re-recitations by a clean reciter (Mandour), a class of children repeating after the reciter (Luqman), formula-dense sequential chapters (Juz' Amma: 37 Basmalas), and 16 minutes of non-Quran audio across three prayers.

Known gaps, to be closed in later configs:

- Isti'adha: one instance corpus-wide (`afasy-baqarah`); the formula lane cannot tell a system that handles it from one that does not.
- Chapter revisits outside prayer: out-of-order chapters exist only in the prayer captures, so order handling is confounded with `content = prayer`.
- Additive noise (traffic, crowd): `noisy` today means limiter-crushed and reverberant (the Luhaidan trio), a rough capture (`mandour-taha`), or a band-limited low-bitrate transfer (`minshawi-luqman`).
- Voices: apart from the children repeating in `minshawi-luqman`, every voice is a trained adult male into a studio or PA chain. No women, no learners reciting alone, no phone or home captures.
- One recording (`afasy-baqarah`) is 36% of the words.

## License

CC BY 4.0 for the annotations and descriptive columns; the audio recordings are not covered.