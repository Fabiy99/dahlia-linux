import 'package:dahlia/src/frame.dart';
import 'package:flutter_test/flutter_test.dart';

String t(String key, [Map<String, Object?> vars = const {}]) =>
    vars.isEmpty ? key : '$key ${vars.entries.map((e) => '${e.key}=${e.value}').join(' ')}';

void main() {
  test('photo frame intervals read as seconds and minutes', () {
    expect(frameIntervalLabel(10, t), 'frame.seconds n=10');
    expect(frameIntervalLabel(60, t), 'frame.minute');
    expect(frameIntervalLabel(300, t), 'frame.minutes n=5');
    expect(frameIntervalLabel(90, t), 'frame.minutes n=1.5');
  });
}
