import 'package:dahlia/src/notices.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('parse keeps valid entries, newest first', () {
    final list = parseNotices({
      'items': [
        {'time': 1, 'level': 'info', 'title': 'a', 'message': ''},
        {'time': 5, 'level': 'odd', 'title': 'b'},
        {'time': 'x'},
        'broken',
      ]
    });
    expect(list.map((n) => [n.time, n.level, n.title]), [
      [5, 'info', 'b'],
      [1, 'info', 'a'],
    ]);
    expect(parseNotices(null), isEmpty);
  });

  test('fresh notices: oldest first, at most three', () {
    final all = [for (var t = 10; t > 0; t--) Notice(t, 'info', '$t', '')];
    expect(freshNotices(all, 8).map((n) => n.time), [9, 10]);
    expect(freshNotices(all, 0).map((n) => n.time), [8, 9, 10]);
    expect(freshNotices(all, 10), isEmpty);
    expect(newestTime(all, 0), 10);
    expect(newestTime(const [], 7), 7);
  });
}
