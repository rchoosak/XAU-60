import pandas as pd

from indicators import calculate_macd


def test_calculate_macd_returns_three_series_with_expected_length() -> None:
    # Use deterministic rising prices so MACD has non-empty numeric output after warmup.
    closes = [float(i) for i in range(1, 200)]
    df = pd.DataFrame({"close": closes})

    macd_line, signal_line, histogram = calculate_macd(df)

    assert len(macd_line) == len(df)
    assert len(signal_line) == len(df)
    assert len(histogram) == len(df)

    # Ensure there are computed values (not all NaN due to warmup).
    assert macd_line.notna().sum() > 0
    assert signal_line.notna().sum() > 0
    assert histogram.notna().sum() > 0
