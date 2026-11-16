// Start screen layout as the server stores it in home.json (dahlia_dashboard/home.py):
// tiles in order with size and visibility, the look and the weather places.
// The browser dashboard reads the same file, so both show the same start screen.

/// Tile sizes: CSS flex basis and grow of .home-tiles > .tile.size-* in style.css.
const tileSizes = ['s', 'm', 'l', 'xl'];
const _flex = {'s': (260.0, 1.0), 'm': (340.0, 1.0), 'l': (520.0, 2.0), 'xl': (double.infinity, 1.0)};

/// Choices of home.py LOOK_CHOICES.
const lookChoices = {
  'theme': ['auto', 'light', 'dark'],
  'accent': ['dahlia', 'violet', 'blue', 'teal', 'amber'],
  'background': ['bloom', 'still', 'off'],
};

class HomeTile {
  const HomeTile(this.id, this.size, this.on);

  final String id;
  final String size;
  final bool on;

  HomeTile copyWith({String? size, bool? on}) => HomeTile(id, size ?? this.size, on ?? this.on);

  Map<String, Object> toJson() => {'id': id, 'size': size, 'on': on};

  @override
  bool operator ==(Object other) => other is HomeTile && other.id == id && other.size == size && other.on == on;

  @override
  int get hashCode => Object.hash(id, size, on);
}

class Look {
  const Look({this.theme = 'auto', this.accent = 'dahlia', this.background = 'bloom', this.weatherFx = true});

  final String theme, accent, background;
  final bool weatherFx;

  factory Look.fromJson(Object? j) {
    if (j is! Map) return const Look();
    String pick(String k, String fallback) {
      final v = j[k];
      return v is String && lookChoices[k]!.contains(v) ? v : fallback;
    }

    return Look(
      theme: pick('theme', 'auto'),
      accent: pick('accent', 'dahlia'),
      background: pick('background', 'bloom'),
      weatherFx: j['weather_fx'] != false,
    );
  }

  Map<String, Object> toJson() => {'theme': theme, 'accent': accent, 'background': background, 'weather_fx': weatherFx};

  /// A copy with one part changed; part is a key of [toJson].
  Look with_(String part, Object value) => Look.fromJson({...toJson(), part: value});

  @override
  bool operator ==(Object other) =>
      other is Look && other.theme == theme && other.accent == accent && other.background == background && other.weatherFx == weatherFx;

  @override
  int get hashCode => Object.hash(theme, accent, background, weatherFx);
}

class HomeConfig {
  const HomeConfig({this.tiles = const [], this.look = const Look(), this.places = const []});

  final List<HomeTile> tiles;
  final Look look;
  final List<Map> places;

  factory HomeConfig.fromJson(Object? j) {
    if (j is! Map) return const HomeConfig();
    final tiles = <HomeTile>[];
    for (final t in j['tiles'] is List ? j['tiles'] as List : const []) {
      if (t is Map && t['id'] is String && tileSizes.contains(t['size'])) {
        tiles.add(HomeTile(t['id'] as String, t['size'] as String, t['on'] == true));
      }
    }
    return HomeConfig(
      tiles: tiles,
      look: Look.fromJson(j['look']),
      places: [for (final p in j['places'] is List ? j['places'] as List : const []) if (p is Map) p],
    );
  }

  List<HomeTile> get shown => [for (final t in tiles) if (t.on) t];

  bool isOn(String id) => tiles.any((t) => t.id == id && t.on);

  List<Map<String, Object>> tilesJson(List<HomeTile> list) => [for (final t in list) t.toJson()];

  /// Tiles with [id] changed.
  List<HomeTile> change(String id, {String? size, bool? on}) =>
      [for (final t in tiles) t.id == id ? t.copyWith(size: size, on: on) : t];

  /// Tiles with [id] moved directly before (or after) [target].
  List<HomeTile> place(String id, String target, {bool after = false}) {
    final list = [...tiles];
    final i = list.indexWhere((t) => t.id == id);
    if (i < 0 || id == target) return list;
    final tile = list.removeAt(i);
    final at = list.indexWhere((t) => t.id == target);
    if (at < 0) return tiles;
    list.insert(after ? at + 1 : at, tile);
    return list;
  }

  /// Tiles with [id] moved one place among the shown tiles; null at the ends.
  List<HomeTile>? move(String id, int step) {
    final s = shown;
    final i = s.indexWhere((t) => t.id == id);
    final j = i + step;
    if (i < 0 || j < 0 || j >= s.length) return null;
    return place(id, s[j].id, after: step > 0);
  }
}

/// Rows of a CSS flex-wrap line-up: which tiles share a row and how wide each
/// is, so the app breaks rows exactly where the browser does.
List<List<(int, double)>> flexRows(List<String> sizes, double width, {double gap = 20}) {
  final rows = <List<(int, double)>>[];
  var row = <int>[];
  var used = 0.0;
  void flush() {
    if (row.isEmpty) return;
    final bases = [for (final i in row) _basis(sizes[i], width)];
    final grows = [for (final i in row) _flex[sizes[i]]!.$2];
    final free = width - bases.fold(0.0, (a, b) => a + b) - gap * (row.length - 1);
    final grow = grows.fold(0.0, (a, b) => a + b);
    rows.add([for (var k = 0; k < row.length; k++) (row[k], bases[k] + free * grows[k] / grow)]);
    row = [];
    used = 0;
  }

  for (var i = 0; i < sizes.length; i++) {
    final b = _basis(sizes[i], width);
    final need = row.isEmpty ? b : used + gap + b;
    if (row.isNotEmpty && need > width) flush();
    used = row.isEmpty ? b : used + gap + b;
    row.add(i);
  }
  flush();
  return rows;
}

/// A basis wider than the row shrinks to it (min-width: 0 in style.css).
double _basis(String size, double width) {
  final b = (_flex[size] ?? _flex['s']!).$1;
  return b > width ? width : b;
}
