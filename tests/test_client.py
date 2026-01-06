"""Tests for the FizzyClient class."""

import sys
from pathlib import Path

import httpx
import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from fizzy_sync import FizzyClient


class TestFizzyClient:
    """Tests for FizzyClient class."""

    def test_init(self):
        """Test client initialization."""
        client = FizzyClient(
            base_url="http://localhost:3000",
            account_slug="12345",
            api_token="test-token",
        )
        assert client.base_url == "http://localhost:3000"
        assert client.account_slug == "12345"
        assert "Bearer test-token" in client.headers["Authorization"]
        client.close()

    def test_init_strips_trailing_slash(self):
        """Test that trailing slash is stripped from base_url."""
        client = FizzyClient(
            base_url="http://localhost:3000/",
            account_slug="12345",
            api_token="test-token",
        )
        assert client.base_url == "http://localhost:3000"
        client.close()

    def test_account_path(self):
        """Test account path building."""
        client = FizzyClient(
            base_url="http://localhost:3000",
            account_slug="12345",
            api_token="test-token",
        )
        assert client._account_path("/boards") == "/12345/boards"
        assert client._account_path("/cards/1") == "/12345/cards/1"
        client.close()


class TestFizzyClientRetry:
    """Tests for FizzyClient retry logic."""

    def test_retry_on_500(self, httpx_mock):
        """Test retry on 500 error."""
        # First two calls return 500, third succeeds
        httpx_mock.add_response(status_code=500)
        httpx_mock.add_response(status_code=500)
        httpx_mock.add_response(json={"status": "ok"})

        client = FizzyClient(
            base_url="http://test",
            account_slug="123",
            api_token="token",
        )
        # Override backoff for faster tests
        client.RETRY_BACKOFF_FACTOR = 0.01

        response = client._request("GET", "/test")
        assert response.json() == {"status": "ok"}

        # Should have made 3 requests
        assert len(httpx_mock.get_requests()) == 3
        client.close()

    def test_retry_on_429_rate_limit(self, httpx_mock):
        """Test retry on 429 rate limit."""
        httpx_mock.add_response(
            status_code=429,
            headers={"Retry-After": "0.01"},
        )
        httpx_mock.add_response(json={"status": "ok"})

        client = FizzyClient(
            base_url="http://test",
            account_slug="123",
            api_token="token",
        )
        client.RETRY_BACKOFF_FACTOR = 0.01

        response = client._request("GET", "/test")
        assert response.json() == {"status": "ok"}
        client.close()

    def test_max_retries_exceeded(self, httpx_mock):
        """Test that max retries raises error."""
        # Always return 500
        httpx_mock.add_response(status_code=500)
        httpx_mock.add_response(status_code=500)
        httpx_mock.add_response(status_code=500)
        httpx_mock.add_response(status_code=500)

        client = FizzyClient(
            base_url="http://test",
            account_slug="123",
            api_token="token",
        )
        client.RETRY_BACKOFF_FACTOR = 0.01

        with pytest.raises(httpx.HTTPStatusError):
            client._request("GET", "/test")

        # Should have made MAX_RETRIES + 1 requests
        assert len(httpx_mock.get_requests()) == client.MAX_RETRIES + 1
        client.close()

    def test_no_retry_on_404(self, httpx_mock):
        """Test that 404 is not retried."""
        httpx_mock.add_response(status_code=404)

        client = FizzyClient(
            base_url="http://test",
            account_slug="123",
            api_token="token",
        )

        with pytest.raises(httpx.HTTPStatusError):
            client._request("GET", "/test")

        # Should have made only 1 request (no retry)
        assert len(httpx_mock.get_requests()) == 1
        client.close()

    def test_404_allowed(self, httpx_mock):
        """Test that 404 can be allowed."""
        httpx_mock.add_response(status_code=404)

        client = FizzyClient(
            base_url="http://test",
            account_slug="123",
            api_token="token",
        )

        response = client._request("GET", "/test", allow_404=True)
        assert response.status_code == 404
        client.close()


class TestFizzyClientAPI:
    """Tests for FizzyClient API methods."""

    def test_get_identity(self, httpx_mock):
        """Test get_identity API call."""
        httpx_mock.add_response(
            json={
                "id": "user-123",
                "email_address": "test@example.com",
                "accounts": [{"id": "acc-1", "name": "Test"}],
            }
        )

        client = FizzyClient(
            base_url="http://test",
            account_slug="123",
            api_token="token",
        )

        identity = client.get_identity()
        assert identity["id"] == "user-123"
        assert len(identity["accounts"]) == 1

        request = httpx_mock.get_request()
        assert request.url.path == "/my/identity"
        client.close()

    def test_list_boards(self, httpx_mock):
        """Test list_boards API call."""
        httpx_mock.add_response(
            json=[
                {"id": "board-1", "name": "Board 1"},
                {"id": "board-2", "name": "Board 2"},
            ]
        )

        client = FizzyClient(
            base_url="http://test",
            account_slug="123",
            api_token="token",
        )

        boards = client.list_boards()
        assert len(boards) == 2
        assert boards[0]["name"] == "Board 1"

        request = httpx_mock.get_request()
        assert request.url.path == "/123/boards"
        client.close()

    def test_create_card(self, httpx_mock):
        """Test create_card API call."""
        httpx_mock.add_response(
            status_code=201,
            json={"number": 42, "title": "Test Card"},
        )

        client = FizzyClient(
            base_url="http://test",
            account_slug="123",
            api_token="token",
        )

        result = client.create_card(
            board_id="board-1",
            title="Test Card",
            description="Test description",
        )
        assert result["number"] == 42

        request = httpx_mock.get_request()
        assert request.url.path == "/123/boards/board-1/cards"
        client.close()

    def test_triage_card(self, httpx_mock):
        """Test triage_card API call."""
        httpx_mock.add_response(status_code=200, json={})

        client = FizzyClient(
            base_url="http://test",
            account_slug="123",
            api_token="token",
        )

        client.triage_card(number=42, column_id="col-1")

        request = httpx_mock.get_request()
        assert request.url.path == "/123/cards/42/triage"
        client.close()

    def test_get_board(self, httpx_mock):
        """Test get_board API call."""
        httpx_mock.add_response(json={"id": "board-1", "name": "My Board"})

        client = FizzyClient(
            base_url="http://test",
            account_slug="123",
            api_token="token",
        )

        board = client.get_board("board-1")
        assert board["name"] == "My Board"

        request = httpx_mock.get_request()
        assert request.url.path == "/123/boards/board-1"
        client.close()

    def test_list_columns(self, httpx_mock):
        """Test list_columns API call."""
        httpx_mock.add_response(
            json=[
                {"id": "col-1", "name": "Doing"},
                {"id": "col-2", "name": "Done"},
            ]
        )

        client = FizzyClient(
            base_url="http://test",
            account_slug="123",
            api_token="token",
        )

        columns = client.list_columns("board-1")
        assert len(columns) == 2
        assert columns[0]["name"] == "Doing"

        request = httpx_mock.get_request()
        assert request.url.path == "/123/boards/board-1/columns"
        client.close()

    def test_create_column(self, httpx_mock):
        """Test create_column API call."""
        httpx_mock.add_response(
            status_code=201,
            json={"id": "col-new", "name": "New Column"},
        )

        client = FizzyClient(
            base_url="http://test",
            account_slug="123",
            api_token="token",
        )

        result = client.create_column("board-1", name="New Column", color="#FF0000")
        assert result["id"] == "col-new"

        request = httpx_mock.get_request()
        assert request.url.path == "/123/boards/board-1/columns"
        client.close()

    def test_delete_column(self, httpx_mock):
        """Test delete_column API call."""
        httpx_mock.add_response(status_code=204)

        client = FizzyClient(
            base_url="http://test",
            account_slug="123",
            api_token="token",
        )

        client.delete_column("board-1", "col-1")

        request = httpx_mock.get_request()
        assert request.url.path == "/123/boards/board-1/columns/col-1"
        assert request.method == "DELETE"
        client.close()

    def test_create_board(self, httpx_mock):
        """Test create_board API call."""
        httpx_mock.add_response(
            status_code=201,
            json={"id": "board-new", "name": "New Board"},
        )

        client = FizzyClient(
            base_url="http://test",
            account_slug="123",
            api_token="token",
        )

        result = client.create_board("New Board")
        assert result["id"] == "board-new"

        request = httpx_mock.get_request()
        assert request.url.path == "/123/boards"
        client.close()

    def test_update_card(self, httpx_mock):
        """Test update_card API call."""
        httpx_mock.add_response(json={"number": 42, "title": "Updated"})

        client = FizzyClient(
            base_url="http://test",
            account_slug="123",
            api_token="token",
        )

        result = client.update_card(42, title="Updated", description="New desc")
        assert result["title"] == "Updated"

        request = httpx_mock.get_request()
        assert request.url.path == "/123/cards/42"
        assert request.method == "PUT"
        client.close()

    def test_close_card(self, httpx_mock):
        """Test close_card API call."""
        httpx_mock.add_response(status_code=200, json={})

        client = FizzyClient(
            base_url="http://test",
            account_slug="123",
            api_token="token",
        )

        client.close_card(42)

        request = httpx_mock.get_request()
        assert request.url.path == "/123/cards/42/closure"
        assert request.method == "POST"
        client.close()

    def test_reopen_card(self, httpx_mock):
        """Test reopen_card API call."""
        httpx_mock.add_response(status_code=200, json={})

        client = FizzyClient(
            base_url="http://test",
            account_slug="123",
            api_token="token",
        )

        client.reopen_card(42)

        request = httpx_mock.get_request()
        assert request.url.path == "/123/cards/42/closure"
        assert request.method == "DELETE"
        client.close()

    def test_list_tags(self, httpx_mock):
        """Test list_tags API call."""
        httpx_mock.add_response(
            json=[
                {"id": "tag-1", "title": "bug"},
                {"id": "tag-2", "title": "feature"},
            ]
        )

        client = FizzyClient(
            base_url="http://test",
            account_slug="123",
            api_token="token",
        )

        tags = client.list_tags()
        assert len(tags) == 2
        assert tags[0]["title"] == "bug"

        request = httpx_mock.get_request()
        assert request.url.path == "/123/tags"
        client.close()

    def test_toggle_tag(self, httpx_mock):
        """Test toggle_tag API call."""
        httpx_mock.add_response(status_code=200, json={})

        client = FizzyClient(
            base_url="http://test",
            account_slug="123",
            api_token="token",
        )

        client.toggle_tag(42, "bug")

        request = httpx_mock.get_request()
        assert request.url.path == "/123/cards/42/taggings"
        assert request.method == "POST"
        client.close()

    def test_get_card(self, httpx_mock):
        """Test get_card API call."""
        httpx_mock.add_response(json={"number": 42, "title": "Test Card"})

        client = FizzyClient(
            base_url="http://test",
            account_slug="123",
            api_token="token",
        )

        card = client.get_card(42)
        assert card["number"] == 42

        request = httpx_mock.get_request()
        assert request.url.path == "/123/cards/42"
        client.close()

    def test_get_card_not_found(self, httpx_mock):
        """Test get_card returns None for 404."""
        httpx_mock.add_response(status_code=404)

        client = FizzyClient(
            base_url="http://test",
            account_slug="123",
            api_token="token",
        )

        card = client.get_card(999)
        assert card is None
        client.close()

    def test_list_cards(self, httpx_mock):
        """Test list_cards API call."""
        httpx_mock.add_response(
            json=[
                {"number": 1, "title": "Card 1"},
                {"number": 2, "title": "Card 2"},
            ]
        )

        client = FizzyClient(
            base_url="http://test",
            account_slug="123",
            api_token="token",
        )

        cards = client.list_cards("board-1")
        assert len(cards) == 2

        request = httpx_mock.get_request()
        assert "board_id=board-1" in str(request.url)
        client.close()

    def test_delete_card(self, httpx_mock):
        """Test delete_card API call."""
        httpx_mock.add_response(status_code=204)

        client = FizzyClient(
            base_url="http://test",
            account_slug="123",
            api_token="token",
        )

        client.delete_card(42)

        request = httpx_mock.get_request()
        assert request.url.path == "/123/cards/42"
        assert request.method == "DELETE"
        client.close()
