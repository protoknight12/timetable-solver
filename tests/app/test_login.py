from app import create_app


def test_login_page_shows_form():
    response = create_app().test_client().get("/login")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'name="username"' in html
    assert 'name="password"' in html


def test_login_post_shows_form_until_be05():
    client = create_app().test_client()
    response = client.post("/login", data={"username": "a", "password": "b"})
    assert response.status_code == 200


def test_pages_extend_base_layout():
    client = create_app().test_client()
    for url in ("/", "/login"):
        html = client.get(url).get_data(as_text=True)
        assert 'lang="bg"' in html
        assert 'id="theme-toggle"' in html
