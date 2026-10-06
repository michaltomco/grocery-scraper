import unittest
from unittest.mock import Mock, patch
import requests
from scrapers.common import fetch_kupi_html

class FetchRetryTests(unittest.TestCase):
    def test_timeout_is_retried_then_returns_live_body(self):
        response = Mock(text='live discounts')
        with patch('scrapers.common.requests.get', side_effect=[requests.Timeout('slow'), response]) as get, patch('time.sleep') as sleep:
            self.assertEqual(fetch_kupi_html('https://www.kupi.cz/test'), 'live discounts')
            self.assertEqual(get.call_count, 2)
            sleep.assert_called_once_with(2)
            response.raise_for_status.assert_called_once()

    def test_repeated_timeouts_stop_after_three_attempts(self):
        with patch('scrapers.common.requests.get', side_effect=requests.Timeout('slow')) as get, patch('time.sleep'):
            with self.assertRaises(requests.Timeout):
                fetch_kupi_html('https://www.kupi.cz/test')
            self.assertEqual(get.call_count, 3)

    def test_forbidden_is_not_retried(self):
        response = Mock()
        response.raise_for_status.side_effect = requests.HTTPError('403 Forbidden')
        with patch('scrapers.common.requests.get', return_value=response) as get, patch('time.sleep') as sleep:
            with self.assertRaises(requests.HTTPError):
                fetch_kupi_html('https://www.kupi.cz/test')
            get.assert_called_once()
            sleep.assert_not_called()

if __name__ == '__main__':
    unittest.main()
