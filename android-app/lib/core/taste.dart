// Taste compatibility between two listeners' top-artist lists.
class SharedArtist {
  final String name;
  final int mine;
  final int theirs;
  const SharedArtist(this.name, this.mine, this.theirs);
  int get combined => mine + theirs;
}

class TasteResult {
  final int score;
  final List<SharedArtist> shared;
  const TasteResult(this.score, this.shared);
}

int _num(dynamic v) {
  final n = int.tryParse('$v') ?? 0;
  return n > 0 ? n : 0;
}

TasteResult tasteMatch(List mine, List theirs) {
  final a = <String, MapEntry<String, int>>{};
  for (final t in mine) {
    if (t is! Map) continue;
    final key = '${t['name'] ?? ''}'.toLowerCase().trim();
    if (key.isEmpty || a.containsKey(key)) continue;
    a[key] = MapEntry('${t['name']}', _num(t['playcount']));
  }
  final b = <String, MapEntry<String, int>>{};
  for (final t in theirs) {
    if (t is! Map) continue;
    final key = '${t['name'] ?? ''}'.toLowerCase().trim();
    if (key.isEmpty || b.containsKey(key)) continue;
    b[key] = MapEntry('${t['name']}', _num(t['playcount']));
  }

  var sharedWeight = 0.0;
  var totalWeight = 0;
  final shared = <SharedArtist>[];
  for (final e in a.entries) {
    final y = b[e.key];
    final w = e.value.value + (y?.value ?? 0);
    totalWeight += w;
    if (y != null) {
      final mx = e.value.value > y.value ? e.value.value : y.value;
      final sim = mx > 0 ? (e.value.value < y.value ? e.value.value : y.value) / mx : 0.0;
      sharedWeight += w * sim;
      shared.add(SharedArtist(e.value.key, e.value.value, y.value));
    }
  }
  for (final e in b.entries) {
    if (!a.containsKey(e.key)) totalWeight += e.value.value;
  }
  shared.sort((x, y) => y.combined.compareTo(x.combined));
  final score = totalWeight > 0 ? ((sharedWeight / totalWeight) * 100).round() : 0;
  return TasteResult(score, shared.take(10).toList());
}

String tasteLabel(int score) {
  if (score >= 80) return 'Musical soulmates';
  if (score >= 60) return 'Strong overlap';
  if (score >= 40) return 'Shared wavelength';
  if (score >= 20) return 'Some common ground';
  if (score >= 1) return 'Mostly different worlds';
  return 'No overlap yet';
}
