// Photo frame on the monitor attached to the server (/api/frame).

import 'format.dart';

/// Seconds per photo the settings offer.
const frameIntervals = [10, 30, 60, 300, 900];

String frameIntervalLabel(int n, Translate t) {
  if (n < 60) return t('frame.seconds', {'n': n});
  if (n == 60) return t('frame.minute');
  final m = (n / 6).round() / 10;
  return t('frame.minutes', {'n': m == m.roundToDouble() ? m.round() : m});
}
