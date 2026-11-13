from app import create_app


def test_login_page_shows_form():
    response = create_app().test_client().get("/login")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'name="username"' in html
    assert 'name="password"' in html


def test_pages_extend_base_layout():
    client = create_app().test_client()
    for url in ("/", "/login"):
        html = client.get(url).get_data(as_text=True)
        assert 'lang="bg"' in html
        assert 'id="theme-toggle"' in html
