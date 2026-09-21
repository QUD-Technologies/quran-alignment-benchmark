<details class="format">
  <summary>Alignment format & submission guide</summary>
  <h3>What to upload</h3>
  <p>Run your system on each recording's audio and export its predicted segments. Upload one <code>&lt;id&gt;.json</code> per recording, individually or together in a ZIP. Use the IDs in Dataset, such as <code>jaber-inshiqaq.json</code>. Upload prediction files only, not audio or ground truth.</p>
  <p>The website supplies the recording ID from the filename and manages schema, scorer, corpus, and submission versions. You do not need <code>submission.json</code>. Existing files containing matching <code>case_id</code> and <code>schema_version: 1</code> are also accepted.</p>
  <h3>A complete file example</h3>
  <pre>{JSON.stringify({segments:[
    {start_s:0.98,end_s:4.84,reference:'Basmala'},
    {start_s:5.78,end_s:22.30,reference:'84:1:1-84:5:3',confidence:0.97},
    {start_s:23.42,end_s:33.12,reference:'84:6:1-84:6:8',confidence:0.99},
    {start_s:34.20,end_s:44.33,reference:'84:7:1-84:8:4',confidence:0.91},
    {start_s:45,end_s:47.10,reference:null},
    {start_s:47.50,end_s:52.53,reference:'84:6:1-84:7:4',confidence:0.91}
  ],runtime_seconds:3.2},null,2)}</pre>
  <p>The last segment returns to words claimed earlier. This new timed segment represents the repeated recitation. Times in this example illustrate the format; generate predictions from your own system.</p>
  <h3>References: always include both ends</h3>
  <p><code>S:A:W</code> means surah, ayah (verse), and word. Numbers start at 1; words are numbered within each ayah using the dataset's Hafs tokenization.</p>
  <div class="table-scroll"><table class="format-table"><thead><tr><th>Claim</th><th>Write this</th></tr></thead><tbody>
    <tr><td>One word</td><td><code>2:5:1-2:5:1</code></td></tr>
    <tr><td>Several words or ayahs</td><td><code>2:5:1-2:6:3</code></td></tr>
    <tr><td>All of ayah 84:1</td><td><code>84:1:1-84:1:3</code></td></tr>
    <tr><td>Opening formulas</td><td><code>"Basmala"</code> or <code>"Isti'adha"</code></td></tr>
    <tr><td>Non-Quran audio</td><td><code>null</code>, without quotes</td></tr>
  </tbody></table></div>
  <p><strong>Not accepted:</strong> <code>2:5:1</code> alone, <code>84:1</code>, or <code>84:1-84:5</code>. A single word still needs the full start-end form. Spans must stay within one surah and move forward in reading order; split a surah change or repeat into separate segments.</p>
  <p>An opening Basmala can be written as <code>"Basmala"</code> or <code>"1:1:1-1:1:4"</code> anywhere; the scorer treats them equivalently. Isti'adha must use the exact spelling above.</p>
  <h3>Timing and segmentation</h3>
  <ul><li><code>start_s</code> and <code>end_s</code> are finite numbers in seconds from the recording start, not milliseconds. Start must be at least 0 and end must be greater than start.</li><li>Order segments by <code>start_s</code>. Consecutive segments can overlap by at most 0.5 seconds. No segment may end beyond the recording duration plus the 1-second validation allowance.</li><li>Use your system's native granularity: per word, ayah, stop, or another grouping. Do not merge or split merely to imitate the reference segmentation.</li><li>Use a new timed segment for a repeated passage; one forward span cannot express two takes. An explicit <code>"segments": []</code> is valid if the system predicts nothing; it receives the corresponding score. A missing file is not an empty prediction.</li></ul>
  <h3>Optional confidence and runtime</h3>
  <p><code>confidence</code> is the system's score from 0 to 1 for whether a segment is safe to use as supplied. Scores of at least 0.80 are green, 0.60 to below 0.80 are amber, and below 0.60 are red. Either every Quran-span segment includes it or none does, consistently across all recordings. Confidence on Basmala, Isti'adha, and null segments is ignored.</p>
  <p><code>runtime_seconds</code> is the total positive processing time for that recording. Supply it for every recording or omit it everywhere. If included, choose the CPU or GPU run profile and describe the hardware in System details. Remove both optional fields if you do not report them.</p>
  <h3>Validation, preview, and publication</h3>
  <p>Files validate as you upload. Each recording shows its own status and actionable errors; submission-wide consistency checks appear below. Unknown fields, invalid references, duplicates within an upload, and unknown recording IDs are rejected. Drop a corrected file with the same ID to replace it.</p>
  <p>You can preview an incomplete set. Publishing requires a valid file for every recording in the latest corpus, completed system details, Hugging Face sign-in, and your explicit confirmation after reviewing the scores. Resubmitting replaces only your system's selected CPU or GPU result. To submit both, keep the same system name, switch the run profile, and upload the other run's files. Files and identity stay private; your chosen system details and scores become public.</p>
  <p>Do not use benchmark annotations, known-passage hints, training or tuning on this corpus, or manual prediction corrections for a real submission. Documented user-facing controls are allowed. Local scoring is also available from the repository linked above.</p>
</details>
