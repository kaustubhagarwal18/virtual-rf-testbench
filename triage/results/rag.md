# Triage: retrieval against the no-retrieval baseline

Model: `qwen2.5:3b`, temperature 0, fixed seed. Scored on whether the model named the fix that resolves each failure. Counts, not percentages: nine questions.

```mermaid
flowchart LR
    run["Saved run<br/>results, logs, sequence"] --> ev["evidence.py<br/>first failure"]
    ev --> pr["prompt.py<br/>instructions + fix list"]
    pr --> llm["qwen2.5:3b<br/>(Ollama, local)"]
    llm --> an["answer.py<br/>parse + refuse unusable"]
    an --> score["evaluate.py<br/>right fix?"]
    label["label.json"] -. answer key, never shown .-> score
```

## error only: 4 of 9

| Fault | Expected | Chosen | | Error shown → the model's answer |
|---|---|---|---|---|
| cable_loss | CABLE | FIRMWARE | ✗ | `Failed: path_loss_100mhz: 1.8898817412 dB, limit 0 to 0.974341649025 dB, margin -0.91554...` -> FIRMWARE (instrument's firmware) because The test results indicate significant path loss across multiple frequencies, which could... |
| config_decimal_error | CONFIGURATION | CONFIGURATION | ✓ | `AssertionError: the generator carries a level offset of 0 dB, but the bench configuration...` -> CONFIGURATION (the bench configuration) because The configuration expects a level offset of 15 dB while the generator carries a... |
| config_parameter_missing | CONFIGURATION | CONFIGURATION | ✓ | `ConfigurationError: BENCH_LEVEL_OFFSET_DB is not set in the bench configuration` -> CONFIGURATION (bench configuration) because ConfigurationError: BENCH_LEVEL_OFFSET_DB is not set in the bench configuration |
| config_parameter_out_of_range | CONFIGURATION | FIRMWARE | ✗ | `ConfigurationError: BENCH_LEVEL_OFFSET_DB=80 is outside the instrument's range of -50 to...` -> FIRMWARE (test code and its configuration) because ConfigurationError suggests a firmware regression due to an out-of-range... |
| link_down | LINK | FIRMWARE | ✗ | `Failed: path_loss_100mhz: 109.243158555 dB, limit 0 to 0.974341649025 dB, margin -108.269...` -> FIRMWARE (instrument's firmware) because The test results indicate significant deviations from the expected limits, suggesting a... |
| link_flaky | LINK | FIRMWARE | ✗ | `Failed: path_loss_1000mhz: 110.881881396 dB, limit 1 to 2 dB, margin -108.882 dB, FAIL` -> FIRMWARE (instrument's firmware) because The test result is outside the acceptable limit, indicating a potential firmware... |
| output_command_change | FIRMWARE | TIMEOUT | ✗ | `drivers.session.InstrumentTimeoutError: TCPIP::127.0.0.1::5025::SOCKET: no reply to...` -> TIMEOUT (raise the session timeout to the instrument's response time) because The session timeout error indicates a VISA Timeout... |
| reset_leaks_offset | FIRMWARE | FIRMWARE | ✓ | `AssertionError: the generator carries a level offset of -2 dB, but the bench...` -> FIRMWARE (the test code and its configuration) because The test code expects a level offset of 0 dB, but the generator carries a... |
| slow_responses | TIMEOUT | TIMEOUT | ✓ | `drivers.session.InstrumentTimeoutError: TCPIP::127.0.0.1::5025::SOCKET: no reply within...` -> TIMEOUT (session timeout) because InstrumentTimeoutError indicates a session timeout issue |

Wrong choices: FIRMWARE x4, TIMEOUT x1.

## full evidence: 3 of 9

| Fault | Expected | Chosen | | Error shown → the model's answer |
|---|---|---|---|---|
| cable_loss | CABLE | FIRMWARE | ✗ | `Failed: path_loss_100mhz: 1.8898817412 dB, limit 0 to 0.974341649025 dB, margin -0.91554...` -> FIRMWARE (analyzer and generator firmware) because Firmware versions have not changed since the last passing run, indicating a... |
| config_decimal_error | CONFIGURATION | FIRMWARE | ✗ | `AssertionError: the generator carries a level offset of 0 dB, but the bench configuration...` -> FIRMWARE (generator firmware) because The generator's firmware is at version 0.1.0, which is unchanged since the last passing... |
| config_parameter_missing | CONFIGURATION | CONFIGURATION | ✓ | `ConfigurationError: BENCH_LEVEL_OFFSET_DB is not set in the bench configuration` -> CONFIGURATION (bench configuration) because ConfigurationError: BENCH_LEVEL_OFFSET_DB is not set in the bench configuration |
| config_parameter_out_of_range | CONFIGURATION | FIRMWARE | ✗ | `ConfigurationError: BENCH_LEVEL_OFFSET_DB=80 is outside the instrument's range of -50 to...` -> FIRMWARE (previous release) because ConfigurationError is due to a configuration setting outside the instrument's range, and the... |
| link_down | LINK | CABLE | ✗ | `Failed: path_loss_100mhz: 109.243158555 dB, limit 0 to 0.974341649025 dB, margin -108.269...` -> CABLE (cable) because The test path loss calibration does not match the cable datasheet, indicating potential issues with the... |
| link_flaky | LINK | CABLE | ✗ | `Failed: path_loss_1000mhz: 110.881881396 dB, limit 1 to 2 dB, margin -108.882 dB, FAIL` -> CABLE (cable and connectors) because The instrument logs indicate that there is no signal reaching the analyzer, suggesting a... |
| output_command_change | FIRMWARE | FIRMWARE | ✓ | `drivers.session.InstrumentTimeoutError: TCPIP::127.0.0.1::5025::SOCKET: no reply to...` -> FIRMWARE (instrument firmware) because The test failures are attributed to the instrument firmware, specifically the 'Undefined... |
| reset_leaks_offset | FIRMWARE | FIRMWARE | ✓ | `AssertionError: the generator carries a level offset of -2 dB, but the bench...` -> FIRMWARE (generator firmware) because The generator's firmware is different from the last passing run, which may have introduced... |
| slow_responses | TIMEOUT | FIRMWARE | ✗ | `drivers.session.InstrumentTimeoutError: TCPIP::127.0.0.1::5025::SOCKET: no reply within...` -> FIRMWARE (instrument firmware) because The test timeout error suggests a potential regression in the firmware, which has not... |

Wrong choices: FIRMWARE x4, CABLE x2.

## full + retrieval: 6 of 9

| Fault | Expected | Chosen | | Error shown → the model's answer |
|---|---|---|---|---|
| cable_loss | CABLE | CABLE | ✓ | `Failed: path_loss_100mhz: 1.8898817412 dB, limit 0 to 0.974341649025 dB, margin -0.91554...` -> CABLE (cable) citing cable_loss-1, cable_loss-2, cable_loss-3 because Extra loss in the cable between generator and analyzer,... |
| config_decimal_error | CONFIGURATION | CONFIGURATION | ✓ | `AssertionError: the generator carries a level offset of 0 dB, but the bench configuration...` -> CONFIGURATION (bench configuration) citing config_decimal_error-1, config_decimal_error-2, config_decimal_error-3 because The... |
| config_parameter_missing | CONFIGURATION | CONFIGURATION | ✓ | `ConfigurationError: BENCH_LEVEL_OFFSET_DB is not set in the bench configuration` -> CONFIGURATION (bench configuration) citing config_parameter_missing-1, config_parameter_missing-3, config_parameter_missing-2... |
| config_parameter_out_of_range | CONFIGURATION | CONFIGURATION | ✓ | `ConfigurationError: BENCH_LEVEL_OFFSET_DB=80 is outside the instrument's range of -50 to...` -> CONFIGURATION (bench configuration) citing config_parameter_out_of_range-1, config_parameter_out_of_range-2,... |
| link_down | LINK | malformed | ✗ | `Failed: path_loss_100mhz: 109.243158555 dB, limit 0 to 0.974341649025 dB, margin -108.269...` -> unusable: '' isn't one of the actions offered: CABLE, LINK, ALIGNMENT, CONFIGURATION, TIMEOUT, TEST_CLEANUP, FIRMWARE, UNKNOWN |
| link_flaky | LINK | CABLE | ✗ | `Failed: path_loss_1000mhz: 110.881881396 dB, limit 1 to 2 dB, margin -108.882 dB, FAIL` -> CABLE (cable and connectors) citing link_flaky-1, link_flaky-2, link_down-3 because The signal disappears for one sweep in a few... |
| output_command_change | FIRMWARE | FIRMWARE | ✓ | `drivers.session.InstrumentTimeoutError: TCPIP::127.0.0.1::5025::SOCKET: no reply to...` -> FIRMWARE (generator) citing output_command_change-1, output_command_change-2, output_command_change-3 because Firmware regression... |
| reset_leaks_offset | FIRMWARE | FIRMWARE | ✓ | `AssertionError: the generator carries a level offset of -2 dB, but the bench...` -> FIRMWARE (generator and analyzer firmware) citing reset_leaks_offset-1, reset_leaks_offset-2, reset_leaks_offset-3 because The... |
| slow_responses | TIMEOUT | FIRMWARE | ✗ | `drivers.session.InstrumentTimeoutError: TCPIP::127.0.0.1::5025::SOCKET: no reply within...` -> FIRMWARE (previous release) citing slow_responses-1, slow_responses-2, slow_responses-3 because The test's instrument timeout is... |

Wrong choices: malformed x1, CABLE x1, FIRMWARE x1.

## Findings

- Best scenario: full + retrieval, 6 of 9.
- Scores in order: error only 4; full evidence 3; full + retrieval 6 (of 9).
- The last step (full evidence to full + retrieval) raised the score by 3 (3 to 6 of 9).
- Most common wrong choice: FIRMWARE (9 times across the scenarios); the model falls back to one answer when unsure.
- Cited the matching past failure yet still chose wrong: link_flaky, slow_responses (2 of the wrong answers). Retrieval surfaced the precedent; the model did not act on it, so the ceiling here is the model, not retrieval.
- Solved in every scenario: config_parameter_missing, reset_leaks_offset — the failures whose error text names the fix almost literally.

These are one machine's deterministic numbers (temperature 0, fixed seed); another machine's model may differ in wording and the odd verdict. A verdict whose reasoning is sound but whose category is wrong still counts as a miss, so the counts, if anything, understate the model.
