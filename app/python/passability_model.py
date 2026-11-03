"""Względny indeks przejezdności: maksimum powiatowe, wagi ze znakiem."""
import math
import statistics


def calculate(rows, weights):
    if not rows or not weights:
        raise ValueError('Potrzebne są komórki i kryteria.')
    if any(not math.isfinite(w) or not -1 <= w <= 1 for w in weights):
        raise ValueError('Wagi muszą być skończone i należeć do [-1,1].')
    if any(len(row) != len(weights) for row in rows):
        raise ValueError('Liczba wartości nie odpowiada liczbie wag.')
    if any(v is None or not math.isfinite(v) or v < 0 for row in rows for v in row):
        raise ValueError('Miary muszą być skończone, nieujemne i kompletne.')
    maxima = [max(row[j] for row in rows) for j in range(len(weights))]
    included = [j for j, maximum in enumerate(maxima) if maximum > 0]
    denominator = len(included)  # Także warstwy z wagą 0, zgodnie z uzgodnionym wzorem.
    if not denominator:
        raise ValueError('Wszystkie kryteria mają maksimum 0.')
    normalized = [[v / maxima[j] if maxima[j] else 0.0 for j, v in enumerate(row)] for row in rows]
    contributions = [[v * weights[j] for j, v in enumerate(row)] for row in normalized]
    scores = [math.fsum(row) / denominator for row in contributions]
    return {'maxima': maxima, 'included': included, 'denominator': denominator,
            'normalized': normalized, 'contributions': contributions, 'scores': scores}


def ranks(values):
    """Średnie rangi przy remisach; brak arbitralnej kolejności równych wyników."""
    ordered = sorted(range(len(values)), key=values.__getitem__)
    result = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and values[ordered[start]] == values[ordered[end]]:
            end += 1
        for i in ordered[start:end]:
            result[i] = (start + end - 1) / 2 + 1
        start = end
    return result


def correlation(a, b):
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    da, db = [v - ma for v in a], [v - mb for v in b]
    divisor = math.sqrt(math.fsum(v*v for v in da) * math.fsum(v*v for v in db))
    return math.fsum(x*y for x, y in zip(da, db)) / divisor if divisor else None


def sign(value):
    return (value > 0) - (value < 0)


def sensitivity(model, weights, fraction=0.2):
    """Zmiana jednej wagi naraz i ścisła obwiednia zmian wszystkich wag ±20%."""
    if not 0 <= fraction <= 1:
        raise ValueError('Ułamek zmiany musi należeć do [0,1].')
    normalized, scores = model['normalized'], model['scores']
    denominator = model['denominator']
    base_ranks = ranks(scores)
    scenarios = []
    limits = [(min(1., max(-1., w*(1-fraction))), min(1., max(-1., w*(1+fraction)))) for w in weights]
    for j in model['included']:
        if weights[j] == 0:
            continue
        for factor, changed in zip([1-fraction, 1+fraction], limits[j]):
            alternative = [score + (changed-weights[j])*row[j]/denominator for score, row in zip(scores, normalized)]
            changes = [abs(a-b) for a, b in zip(scores, alternative)]
            scenarios.append({'index': j, 'factor': factor, 'weight': changed,
                              'clipped': changed != weights[j]*factor,
                              'changed': changed != weights[j],
                              'mean_abs_delta': statistics.fmean(changes),
                              'max_abs_delta': max(changes),
                              'sign_changes': sum(sign(a) != sign(b) for a, b in zip(scores, alternative)),
                              'spearman': correlation(base_ranks, ranks(alternative))})
    low, high = [], []
    for row in normalized:
        low.append(math.fsum(v*min(limits[j]) for j, v in enumerate(row))/denominator)
        high.append(math.fsum(v*max(limits[j]) for j, v in enumerate(row))/denominator)
    stable = [(lo > 0 or hi < 0 or lo == hi == 0) for lo, hi in zip(low, high)]
    return {'scenarios': scenarios, 'low': low, 'high': high, 'stable': stable}
