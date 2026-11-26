"""What the host's OpenSearch cluster and its instances' files contain."""

import json

import pytest
import yaml

import canasta_opensearch_render as render


@pytest.mark.parametrize("value, mb", [
    ("512m", 512), ("768M", 768), ("1g", 1024), ("4G", 4096), (" 2g ", 2048),
])
def test_heap_sizes_are_read_in_megabytes(value, mb):
    assert render.parse_heap(value) == mb


@pytest.mark.parametrize("value", ["", None, "4", "4gb", "0m", "1.5g", "4k", "-1g"])
def test_invalid_heap_sizes_are_none(value):
    assert render.parse_heap(value) is None


def test_the_cluster_heap_is_the_largest_member_setting():
    assert render.heap_mb(["512m", "2g", "", "bogus"]) == 2048


def test_the_cluster_heap_defaults_without_a_setting():
    assert render.heap_mb(["", "bogus"]) == render.DEFAULT_HEAP_MB
    assert render.heap_mb([]) == render.DEFAULT_HEAP_MB


def test_the_cluster_compose_file():
    compose = yaml.safe_load(render.render_compose(1536))
    svc = compose["services"]["opensearch"]
    assert svc["image"] == render.IMAGE
    assert svc["container_name"] == render.CONTAINER
    assert "OPENSEARCH_JAVA_OPTS=-Xms1536m -Xmx1536m" in svc["environment"]
    # Security comes from Canasta's files, never the image's demo setup.
    assert "DISABLE_INSTALL_DEMO_CONFIG=true" in svc["environment"]
    assert "plugins.security.allow_default_init_securityindex=false" in svc["environment"]
    assert "plugins.security.ssl.http.enabled=true" in svc["environment"]
    assert "plugins.security.authcz.admin_dn=CN=%s" % render.ADMIN_CN in svc["environment"]
    # Nothing is published on the host.
    assert "ports" not in svc
    assert "%s:%s:ro" % ("conf", render.CONF_DIR) in svc["volumes"]
    assert compose["networks"]["opensearch"]["name"] == render.NETWORK
    assert compose["volumes"]["data"]["name"] == render.DATA_VOLUME
    assert compose["volumes"]["conf"]["name"] == render.CONF_VOLUME


def test_the_instance_layer_joins_the_cluster_network():
    layer = yaml.safe_load(render.render_layer("/srv/os/ca.pem"))
    web = layer["services"]["web"]
    assert "CANASTA_OPENSEARCH_PASSWORD=${CANASTA_OPENSEARCH_PASSWORD:-}" in web["environment"]
    assert "/srv/os/ca.pem:%s:ro" % render.WEB_CA in web["volumes"]
    assert render.NETWORK in web["networks"]
    assert layer["networks"][render.NETWORK] == {"external": True}


def test_the_global_settings_read_the_password_from_the_environment():
    php = render.render_global_settings("a1b2c3")
    assert php.startswith("<?php\n")
    assert "'username' => 'a1b2c3'" in php
    assert "getenv( 'CANASTA_OPENSEARCH_PASSWORD' )" in php
    assert "'transport' => 'Https'" in php
    assert "CURLOPT_CAINFO => '%s'" % render.WEB_CA in php
    assert "'host' => '%s'" % render.CONTAINER in php
    assert "$wgCirrusSearchWikimediaExtraPlugin = [ 'regex' => [ 'build', 'use' ]" in php
    assert "$wgCirrusSearchUseIcuFolding = 'yes';" in php


def test_index_names_are_the_token_and_the_lowercased_wiki():
    assert render.index_base_name("tok1", "MyWiki") == "tok1_mywiki"


def test_an_unindexed_wiki_keeps_the_database_search():
    php = render.render_wiki_settings("tok1", "Main", False)
    assert "$wgCirrusSearchIndexBaseName = 'tok1_main';" in php
    assert "$wgSearchType" not in php


def test_an_indexed_wiki_searches_with_cirrussearch():
    php = render.render_wiki_settings("tok1", "main", True)
    assert "$wgSearchType = 'CirrusSearch';" in php


def test_php_literals_are_escaped():
    assert render._php("it's \\ here") == "'it\\'s \\\\ here'"
    assert render._php({"a": [True, 2]}) == "[ 'a' => [ true, 2 ] ]"
    with pytest.raises(TypeError):
        render._php(None)


def test_the_security_index_starts_with_no_users_or_mappings():
    for text, kind in ((render.render_internal_users(), "internalusers"),
                       (render.render_roles_mapping(), "rolesmapping")):
        data = json.loads(text)
        assert data == {"_meta": {"type": kind, "config_version": 2}}


def test_an_instance_role_reaches_only_its_own_indexes():
    role = render.instance_role("tok1")
    assert role["index_permissions"] == [
        {"index_patterns": ["tok1_*"], "allowed_actions": ["indices_all"]}]
    assert "cluster_all" not in role["cluster_permissions"]
