import 'package:dahlia/src/home_layout.dart';
import 'package:flutter_test/flutter_test.dart';

const json = {
  'tiles': [
    {'id': 'weather', 'size': 'm', 'on': true},
    {'id': 'apps', 'size': 'l', 'on': true},
    {'id': 'storage', 'size': 's', 'on': true},
    {'id': 'network', 'size': 's', 'on': false},
    {'id': 'system', 'size': 's', 'on': true},
    {'id': 'bad', 'size': 'huge', 'on': true},
  ],
  'look': {'theme': 'dark', 'accent': 'neon', 'weather_fx': false},
  'places': [
    {'name': 'Hamburg', 'latitude': 53.55, 'longitude': 9.99},
  ],
};

void main() {
  test('parses home.json and drops broken parts', () {
    final h = HomeConfig.fromJson(json);
    expect(h.tiles.map((t) => t.id), ['weather', 'apps', 'storage', 'network', 'system']);
    expect(h.shown.map((t) => t.id), ['weather', 'apps', 'storage', 'system']);
    expect(h.isOn('network'), isFalse);
    expect(h.look, const Look(theme: 'dark', weatherFx: false));
    expect(h.places.single['name'], 'Hamburg');
    expect(HomeConfig.fromJson('nonsense').tiles, isEmpty);
    expect(const Look().with_('background', 'off').background, 'off');
    expect(const Look().with_('accent', 'pink').accent, 'dahlia');
  });

  test('moving, resizing and hiding tiles', () {
    final h = HomeConfig.fromJson(json);
    ids(List<HomeTile> l) => l.map((t) => t.id).toList();
    expect(ids(h.place('system', 'weather')), ['system', 'weather', 'apps', 'storage', 'network']);
    expect(ids(h.place('weather', 'apps', after: true)), ['apps', 'weather', 'storage', 'network', 'system']);
    expect(ids(h.place('weather', 'weather')), ids(h.tiles));
    // Moving skips hidden tiles: storage and system swap around network.
    expect(ids(h.move('storage', 1)!), ['weather', 'apps', 'network', 'system', 'storage']);
    expect(h.move('weather', -1), isNull);
    expect(h.move('system', 1), isNull);
    expect(h.change('apps', size: 'xl').firstWhere((t) => t.id == 'apps').size, 'xl');
    expect(h.change('apps', on: false).where((t) => t.on).length, 3);
    expect(h.tilesJson(h.tiles).first, {'id': 'weather', 'size': 'm', 'on': true});
  });

  test('rows break where the browser breaks them', () {
    // 1360 px window: content 1144 px. Weather and apps share a row; the small
    // tiles do not fit next to them.
    final rows = flexRows(['m', 'l', 's', 's', 's'], 1144);
    expect([for (final r in rows) r.map((e) => e.$1).toList()], [
      [0, 1],
      [2, 3, 4],
    ]);
    // Free space goes to the tiles by their grow factor (m 1, l 2).
    final free = 1144 - 340 - 520 - 20;
    expect(rows[0][0].$2, closeTo(340 + free / 3, .001));
    expect(rows[0][1].$2, closeTo(520 + free * 2 / 3, .001));
    expect(rows[1].every((e) => (e.$2 - (1144 - 40) / 3).abs() < .001), isTrue);
    // A full-width tile takes a row of its own; a phone gets one column.
    expect(flexRows(['s', 'xl', 's'], 1144).map((r) => r.length), [1, 1, 1]);
    expect(flexRows(['l', 'm'], 360).map((r) => r.single.$2), [360, 360]);
  });
}
