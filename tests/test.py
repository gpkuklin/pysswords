import argparse
import inspect
import os
import shutil
import sys
import unittest
import yaml
import time
from functools import wraps

import gnupg

__file__ = os.path.relpath(inspect.getsourcefile(lambda _: None))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.relpath(__file__))))
import pysswords
from pysswords.db import Database, Credential
from pysswords.db.credential import CredentialNotFoundError
from pysswords.python_two import *


TEST_DIR = os.path.join(os.path.dirname(os.path.relpath(__file__)))
TEST_DATA_DIR = os.path.join(TEST_DIR, "data")
BENCHMARK = os.environ.get("BENCHMARK")


def timethis(func):
    ''' Decorator that reports the execution time.
    '''
    if BENCHMARK:
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.time()
            result = func(*args, **kwargs)
            end = time.time()
            print("[{:.2f}]".format(end-start), func.__name__)
            return result
        return wrapper
    else:
        return func


def build_keys():
    gpg = gnupg.GPG(homedir="/tmp/pysswords")
    key_input = gpg.gen_key_input(
        name_real="Pysswords",
        name_email="pysswords@pysswords",
        name_comment="Auto-generated by Pysswords",
        key_length=512,
        expire_date=0,
        passphrase="dummy_passphrase"
    )
    key = gpg.gen_key(key_input)
    ascii_armored_public_keys = gpg.export_keys(key)
    ascii_armored_private_keys = gpg.export_keys(key, True)
    with open(os.path.join(TEST_DATA_DIR, "newkey.asc"), 'w') as f:
        f.write(ascii_armored_public_keys)
        f.write(ascii_armored_private_keys)


def mock_create_keyring(path, *args, **kwargs):
    """Import key.asc instead of generating new key
    passphrase used to create the key was 'dummy_database'"""
    gpg = gnupg.GPG(homedir=path)
    with open(os.path.join(TEST_DATA_DIR, "key.asc")) as keyfile:
        gpg.import_keys(keyfile.read())
    return gpg.list_keys()[0]


def mock_gen_key(self, key_input):
    return mock_create_keyring(self.homedir)


def some_credential(**kwargs):
    return pysswords.db.Credential(
        name=kwargs.get("name", "example.com"),
        login=kwargs.get("login", "john.doe"),
        password=kwargs.get("password", "--BEGIN GPG-- X --END GPG--"),
        comment=kwargs.get("comment", "Some comments"),
    )


def some_credential_dict(**kwargs):
    return pysswords.db.credential.asdict(
        some_credential(**kwargs)
    )


def clean(path):
    if os.path.exists(path):
        shutil.rmtree(path)


class CryptTests(unittest.TestCase):

    def setUp(self):
        self.path = os.path.join(TEST_DATA_DIR, "database")
        self.passphrase = "dummy_passphrase"
        self.cleanup()

    def tearDown(self):
        self.cleanup()

    def cleanup(self):
        if os.path.exists(self.path):
            shutil.rmtree(self.path)

    @timethis
    @patch("pysswords.crypt.create_keyring", new=mock_create_keyring)
    def test_create_keyring_adds_gpg_keys_to_path(self):
        keyring_path = os.path.join(self.path, ".keys")
        pysswords.crypt.create_keyring(keyring_path, self.passphrase)
        pubring = os.path.join(keyring_path, "pubring.gpg")
        secring = os.path.join(keyring_path, "secring.gpg")
        self.assertTrue(os.path.isfile(pubring))
        self.assertTrue(os.path.isfile(secring))

    @timethis
    @patch("pysswords.crypt.gnupg.GPG.gen_key", new=mock_gen_key)
    def test_generate_keys_return_valid_key(self):
        key = pysswords.crypt.generate_keys(self.path, self.passphrase)
        self.assertIsNotNone(key)
        self.assertEqual(
            key["fingerprint"],
            '2B88BF1F03FC2E3871894966F77B7A363E2EAE61'
        )

    @timethis
    def test_generate_key_input_returns_batch_string_with_passphrase(self):
        batch = pysswords.crypt.generate_key_input(self.path, self.passphrase)
        self.assertIn("\nPassphrase: {}".format(self.passphrase), batch)

    @timethis
    def test_create_keyring_generate_keys(self):
        self.cleanup()
        with patch("pysswords.crypt.generate_keys") as mocked_generate:
            pysswords.crypt.create_keyring(self.path, self.passphrase)
            self.assertTrue(mocked_generate.called)


class DatabaseTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.path = os.path.join(TEST_DATA_DIR, "database")
        cls.passphrase = "dummy_passphrase"
        to_patch = "pysswords.db.database.create_keyring"
        with patch(to_patch, new=mock_create_keyring):
            cls.database = pysswords.db.Database.create(
                cls.path,
                cls.passphrase
            )

    @classmethod
    def tearDownClass(cls):
        clean(cls.path)

    def setUp(self):
        for cred in (d for d in os.listdir(self.path) if d != ".keys"):
            fullpath = os.path.join(self.path, cred)
            shutil.rmtree(fullpath)

    @timethis
    def test_create_keyring(self):
        self.assertIsInstance(self.database, pysswords.db.Database)
        self.assertTrue(len(self.database.gpg.list_keys()) == 1)

    @timethis
    def test_keys_path_returns_database_path_joined_with_dot_keys(self):
        keys_path = self.database.keys_path
        self.assertEqual(keys_path, os.path.join(self.path, ".keys"))

    @timethis
    def test_add_credential_make_dir_in_dbpath_with_credential_name(self):
        credential = some_credential()
        self.database.add(**credential._asdict())
        credential_dir = os.path.join(self.path, credential.name)
        self.assertTrue(os.path.exists(credential_dir))
        self.assertTrue(os.path.isdir(credential_dir))

    @timethis
    def test_add_credential_createas_pyssword_file_named_after_login(self):
        credential = some_credential()
        self.database.add(**credential._asdict())
        credential_dir = os.path.join(self.path, credential.name)
        credential_filename = "{}.pyssword".format(credential.login)
        credential_file = os.path.join(credential_dir, credential_filename)
        self.assertTrue(os.path.isfile(credential_file))

    @timethis
    def test_add_credential_creates_dir_when_credential_name_is_a_dir(self):
        credential = some_credential(name="emails/misc/example.com")
        emails_dir = os.path.join(self.path, "emails")
        misc_dir = os.path.join(emails_dir, "misc")
        self.database.add(**credential._asdict())
        self.assertTrue(os.path.isdir(emails_dir))
        self.assertTrue(os.path.isdir(misc_dir))

    @timethis
    def test_add_credential_returns_credential(self):
        credential = some_credential_dict()
        returned = self.database.add(**credential)
        self.assertIsInstance(returned, Credential)

    @timethis
    def test_gpg_returns_valid_gnupg_gpg_object(self):
        gpg = self.database.gpg
        self.assertIsInstance(gpg, pysswords.crypt.gnupg.GPG)

    @timethis
    def test_credentials_returns_a_list_of_all_added_credentials(self):
        self.database.add(**some_credential(name="example.com")._asdict())
        self.database.add(**some_credential(name="archive.org")._asdict())
        credentials = self.database.credentials
        self.assertIsInstance(credentials, list)
        self.assertEqual(2, len(credentials))
        for credential in credentials:
            self.assertIsInstance(credential, pysswords.db.Credential)

    @timethis
    def test_add_repeated_credential_without_overwrite_on_raises_error(self):
        credential = some_credential_dict()
        self.database.add(**credential)
        with self.assertRaises(pysswords.db.CredentialExistsError):
            self.database.add(**credential)

    @timethis
    def test_remove_deletes_pysswords_file(self):
        credential = some_credential_dict()
        credential_path = pysswords.db.credential.expandpath(
            self.path,
            credential["name"],
            credential["login"]
        )
        self.database.add(**credential)
        self.assertTrue(os.path.isfile(credential_path))
        self.database.remove(credential["name"], credential["login"])
        self.assertFalse(os.path.isfile(credential_path))

    @timethis
    def test_remove_deletes_pyssword_dir_if_empty_after_deletion(self):
        credential = some_credential_dict()
        credential_path = pysswords.db.credential.expandpath(
            self.path,
            credential["name"],
            credential["login"]
        )
        self.database.add(**credential)
        self.assertTrue(os.path.exists(os.path.dirname(credential_path)))
        self.database.remove(credential["name"], credential["login"])
        self.assertFalse(os.path.exists(os.path.dirname(credential_path)))

    @timethis
    def test_get_credential_by_name_returns_expected_credential(self):
        credential = some_credential(name="example.com")
        self.database.add(**credential._asdict())
        found = self.database.get(name=credential.name)

        self.assertIsInstance(found, list)
        self.assertTrue(all(True for c in found
                            if isinstance(c, pysswords.db.Credential)))
        self.assertTrue(found[0].name, credential.name)

    @timethis
    def test_get_returns_unique_credential_when_login_is_passed(self):
        pwd = "dummy"
        credential = some_credential(
            name="example.com",
            password=pwd
        )
        credential2 = some_credential(
            name="example.com",
            login="jonny.doe"
        )
        with patch("pysswords.db.Database.encrypt", return_value=pwd):
            self.database.add(**credential._asdict())
            self.database.add(**credential2._asdict())
        found = self.database.get(
            name=credential.name,
            login=credential.login
        )

        self.assertEqual(found[0], credential)

    @timethis
    def test_get_returns_no_element_when_name_not_found(self):
        credential = some_credential(name="example.com")
        self.database.add(**credential._asdict())
        found = self.database.get(name="not added name")

        self.assertListEqual(found, [])

    @timethis
    def test_search_database_returns_list_with_matched_credentials(self):
        credential1 = some_credential_dict(name="example.com")
        credential2 = some_credential_dict(name="github.com")
        credential3 = some_credential_dict(name="twitter.com")
        self.database.add(**credential1)
        self.database.add(**credential2)
        self.database.add(**credential3)

        self.assertEqual(len(self.database.search("it")), 2)
        self.assertEqual(len(self.database.search("github")), 1)
        self.assertEqual(len(self.database.search("not there")), 0)

    @timethis
    def test_encrypt_text_returns_valid_encryption_ascii_gpg(self):
        text = "secret"
        encrypted = self.database.encrypt(text)
        self.assertIn("-BEGIN PGP MESSAGE-", encrypted)
        self.assertIn("-END PGP MESSAGE-", encrypted)

    @timethis
    def test_key_returns_expected_key_fingerprint(self):
        self.assertEqual(
            self.database.key(),
            "2B88BF1F03FC2E3871894966F77B7A363E2EAE61")

    @timethis
    def test_key_returns_private_key_when_private_is_true(self):
        mock = Mock()
        mock.return_value = [
            {"fingerprint": "2B88BF1F03FC2E3871894966F77B7A363E2EAE61"}
        ]
        self.database.gpg.list_keys = mock
        self.database.key(private=True)
        self.database.gpg.list_keys.assert_any_call_with(secret=True)

    @timethis
    def test_decrypt_returns_plain_text_data(self):
        text = "secret"
        encrypted = self.database.encrypt(text)
        decrypted = self.database.decrypt(encrypted,
                                          passphrase=self.passphrase)
        self.assertEqual(decrypted, text)

    @timethis
    def test_update_credential_updates_credential_values(self):
        values = some_credential_dict()
        self.database.add(
            name=values["name"],
            login=values["login"],
            password=values["password"],
            comment=values["comment"]
        )
        name = values["name"]
        login = values["login"]
        new_values = values
        new_values["login"] = "doe.john"
        self.database.update(name, login, to_update=new_values)
        found = self.database.get(
            name=new_values["name"],
            login=new_values["login"]
        )

        self.assertEqual(found[0].login, new_values["login"])

    @timethis
    def test_remove_raises_credentialnotfounderror(self):
        with self.assertRaises(CredentialNotFoundError):
            self.database.remove(name="none", login="none")


class CredentialTests(unittest.TestCase):

    def setUp(self):
        self.path = os.path.join(TEST_DATA_DIR, "database")
        self.cleanup()

    def tearDown(self):
        self.cleanup()

    def cleanup(self):
        if os.path.exists(self.path):
            shutil.rmtree(self.path)

    @timethis
    def test_credential_expandpath_returns_expected_path_to_credential(self):
        credential = some_credential()
        credential_path = pysswords.db.credential.expandpath(
            self.path,
            name=credential.name,
            login=credential.login
        )
        expected_path = os.path.join(
            self.path,
            os.path.basename(credential.name),
            "{}.pyssword".format(credential.login)
        )
        self.assertEqual(credential_path, expected_path)

    @timethis
    def test_credential_content_returns_yaml_content_parseable_to_dict(self):
        content = pysswords.db.credential.content(some_credential())
        self.assertEqual(yaml.load(content), some_credential())


class UtilsTests(unittest.TestCase):

    @timethis
    def test_which_handle_windows_exe_extension_for_executables(self):
        with patch("pysswords.utils.os") as mocker:
            mocker.name = "nt"
            mocker.environ = {"PATH": "/"}
            mocker.pathsep = ":"
            mocked_join = Mock()
            mocker.path.join = mocked_join
            pysswords.utils.which("python")
            mocked_join.assert_any_call("/", "python.exe")


class MainTests(unittest.TestCase):

    def setUp(self):
        self.tempdb_path = os.path.join(TEST_DATA_DIR, "tmp")
        self.cleanup()
        self.passphrase = "dummy_passphrase"

    def tearDown(self):
        self.cleanup()

    def cleanup(self):
        if os.path.exists(self.tempdb_path):
            shutil.rmtree(self.tempdb_path)

    def create_database(self):
        with patch("pysswords.db.database.create_keyring",
                   new=mock_create_keyring):
            return Database.create(self.tempdb_path, self.passphrase)

    @timethis
    def test_main_parse_args_returns_argparse_namespace(self):
        args = pysswords.__main__.parse_args(["--init"])
        self.assertIsInstance(args, argparse.Namespace)

    @timethis
    def test_main_default_pyssword_dir(self):
        pysswords_dir = os.path.join(os.path.expanduser("~"), ".pysswords")
        self.assertEqual(pysswords_dir, pysswords.__main__.default_db())

    @timethis
    def test_main_parse_args_has_init_arg(self):
        args = pysswords.__main__.parse_args(["--init"])
        self.assertIn("init", args.__dict__)
        args_short = pysswords.__main__.parse_args(["-I"])
        self.assertIn("init", args_short.__dict__)

    @timethis
    def test_main_parse_args_has_database_arg(self):
        args = pysswords.__main__.parse_args(["--database", "/tmp/pysswords"])
        self.assertIn("database", args.__dict__)
        args_short = pysswords.__main__.parse_args(["-D", "/tmp/pysswords"])
        self.assertIn("database", args_short.__dict__)

    @timethis
    def test_main_parse_args_has_database_default_value(self):
        args = pysswords.__main__.parse_args([])
        self.assertEqual(args.database, pysswords.__main__.default_db())

    @timethis
    def test_main_parse_args_has_add_arg(self):
        args = pysswords.__main__.parse_args(["--add"])
        self.assertIn("add", args.__dict__)
        args_short = pysswords.__main__.parse_args(["-a"])
        self.assertIn("add", args_short.__dict__)

    @timethis
    def test_main_parse_args_add_arg_is_true_when_passed(self):
        args = pysswords.__main__.parse_args(["--add"])
        self.assertTrue(args.add)

    @timethis
    def test_main_parse_args_has_remove_arg(self):
        credential_name = "example.com"
        args = pysswords.__main__.parse_args(["--remove", credential_name])
        args_short = pysswords.__main__.parse_args(["-r", credential_name])
        self.assertIn("remove", args.__dict__)
        self.assertIn("remove", args_short.__dict__)

    @timethis
    def test_main_parse_args_remove_arg_has_credential_name_passed(self):
        credential_name = "example.com"
        args = pysswords.__main__.parse_args(["--remove", credential_name])
        self.assertTrue(args.remove, credential_name)

    @timethis
    def test_main_parse_args_has_update_arg(self):
        credential_name = "example.com"
        args = pysswords.__main__.parse_args(["--update", credential_name])
        args_short = pysswords.__main__.parse_args(["-u", credential_name])
        self.assertIn("update", args.__dict__)
        self.assertIn("update", args_short.__dict__)

    @timethis
    def test_main_parse_args_update_arg_has_credential_name_passed(self):
        credential_name = "example.com"
        args = pysswords.__main__.parse_args(["--update", credential_name])
        self.assertEqual(args.update, credential_name)

    @timethis
    def test_main_parse_args_has_get_arg(self):
        credential_name = "example.com"
        args = pysswords.__main__.parse_args(["--get", credential_name])
        args_short = pysswords.__main__.parse_args(["-g", credential_name])
        self.assertIn("get", args.__dict__)
        self.assertIn("get", args_short.__dict__)

    @timethis
    def test_main_parse_args_has_show_password_arg(self):
        args = pysswords.__main__.parse_args(["--show-password"])
        args_short = pysswords.__main__.parse_args(["-P"])
        self.assertIn("show_password", args.__dict__)
        self.assertIn("show_password", args_short.__dict__)

    @timethis
    def test_main_parse_args_get_arg_has_credential_name_passed(self):
        credential_name = "example.com"
        args = pysswords.__main__.parse_args(["--get", credential_name])
        self.assertEqual(args.get, credential_name)

    @timethis
    def test_main_parse_args_has_search_arg(self):
        credential_name = "example.com"
        args = pysswords.__main__.parse_args(["--search", credential_name])
        args_short = pysswords.__main__.parse_args(["-s", credential_name])
        self.assertIn("search", args.__dict__)
        self.assertIn("search", args_short.__dict__)

    @timethis
    def test_main_parse_args_search_arg_has_credential_name_passed(self):
        credential_name = "example.com"
        args = pysswords.__main__.parse_args(["--search", credential_name])
        self.assertEqual(args.search, credential_name)

    @timethis
    def test_main_raises_error_when_clipboard_passed_without_get_args(self):
        with open(os.devnull, 'w') as devnull:
            with patch("sys.stderr", devnull):
                with self.assertRaises(SystemExit):
                    pysswords.__main__.parse_args(["--clipboard"])
            with patch("sys.stderr", devnull):
                with self.assertRaises(SystemExit):
                    pysswords.__main__.parse_args(["-c"])

    @timethis
    def test_main_calls_cli_constructor_with_init_when_init_passed(self):
        tmp_path = "/tmp/.pysswords"
        args = ["-D", tmp_path, "--init"]
        with patch("pysswords.__main__.CLI") as mocked:
            pysswords.__main__.main(args)
            mocked.assert_called_once_with(
                database_path=tmp_path,
                show_password=False,
                init=True
            )

    @timethis
    def test_main_calls_cli_add_credential_when_add_passed(self):
        args = ["-D", "/tmp/pysswords", "--add"]
        with patch("pysswords.__main__.CLI") as mocked:
            pysswords.__main__.main(args)
            mocked().add_credential.assert_called_once_with()

    @timethis
    def test_main_calls_cli_get_credentials_when_get_passed(self):
        fullname = "john.doe@example.com"
        args = ["-D", "/tmp/pysswords", "--get", fullname]
        with patch("pysswords.__main__.CLI") as mocked:
            pysswords.__main__.main(args)
            mocked().get_credentials.assert_called_once_with(
                fullname=fullname
            )

    @timethis
    def test_main_calls_cli_search_credentials_when_search_passed(self):
        query = "example.com|org|net"
        args = ["-D", "/tmp/pysswords", "--search", query]
        with patch("pysswords.__main__.CLI") as mocked:
            pysswords.__main__.main(args)
            mocked().search_credentials.assert_called_once_with(
                query=query
            )

    @timethis
    def test_main_calls_cli_update_credentials_when_update_passed(self):
        fullname = "john.doe@example.com"
        args = ["-D", "/tmp/pysswords", "--update", fullname]
        with patch("pysswords.__main__.CLI") as mocked:
            pysswords.__main__.main(args)
            mocked().update_credentials.assert_called_once_with(
                fullname=fullname
            )

    @timethis
    def test_main_calls_cli_remove_credentials_when_remove_passed(self):
        fullname = "john.doe@example.com"
        args = ["-D", "/tmp/pysswords", "--remove", fullname]
        with patch("pysswords.__main__.CLI") as mocked:
            pysswords.__main__.main(args)
            mocked().remove_credentials.assert_called_once_with(
                fullname=fullname
            )

    @timethis
    def test_main_calls_cli_show_display_when_nothing_passed(self):
        args = []
        with patch("pysswords.__main__.CLI") as mocked:
            pysswords.__main__.main(args)
            mocked().show_display.assert_called_once_with()

    @timethis
    def test_main_calls_copy_to_clipboard_when_clipboard_passed(self):
        fullname = "john.doe@example.com"
        args = ["-D", "/tmp/pysswords", "--clipboard", "--get", fullname]
        with patch("pysswords.__main__.CLI") as mocked:
            pysswords.__main__.main(args)
            mocked().copy_to_clipboard.assert_called_once_with(
                fullname=fullname
            )


    # @timethis
    # def test_main__handles_with_init_arg_create_database(self):
    #     tempdb_path = os.path.join(self.tempdb_path, "temp")
    #     with patch("pysswords.__main__.Database") as mocked:
    #         with patch("pysswords.__main__.prompt"):
    #             pysswords.__main__.main(["-I", "-D", tempdb_path])
    #             self.assertTrue(mocked.create.called)

    # @timethis
    # def test_cli_main_add_credential_when_passed_add_arg(self):
    #     args = ["-D", "/tmp/pysswords", "-a"]
    #     with patch("pysswords.__main__.Database") as mocked:
    #         with patch("pysswords.__main__.prompt") as mocked_prompt:
    #             mocked_prompt.side_effect = [
    #                 "example.com",
    #                 "doe",
    #                 "pass",
    #                 "No Comment"
    #             ]
    #             pysswords.__main__.main(args)
    #             self.assertIsInstance(
    #                 mocked().add.call_args[0][0],
    #                 Credential
    #             )

    # @timethis
    # def test_prompt_input_uses_default_arg(self):
    #     default = "123123123"
    #     with patch(BUILTINS_NAME + ".input") as mocked:
    #         __main__.prompt("Name", default)
    #         call_args, _ = mocked.call_args
    #         self.assertIn(default, call_args[0])

    # @timethis
    # def test_prompt_with_password_calls_prompt_password(self):
    #     with patch("pysswords.__main__.prompt_password") as mocked:
    #         pysswords.__main__.prompt("Pass:", password=True)
    #         self.assertTrue(mocked.called)

    # @timethis
    # def test_promt_password_returns_entered_password(self):
    #     with patch(BUILTINS_NAME + ".print"):
    #         with patch("pysswords.__main__.getpass") as mocked:
    #             entry = "entry"
    #             mocked.return_value = entry
    #             ret = pysswords.__main__.prompt_password("Pass:")
    #             self.assertEqual(entry, ret)

    # @timethis
    # def test_getpassphrase_raises_value_error_when_passwords_didnt_match(self):
    #     with patch(BUILTINS_NAME + ".print"):
    #         with patch("pysswords.__main__.getpass") as mocked:
    #             mocked.side_effect = ["pass", "wrong"] * 3
    #             with self.assertRaises(ValueError):
    #                 __main__.prompt_password("Password:")

    # @timethis
    # def test_calls_cli_get_credential_when_get_arg_passed(self):
    #     credential = Credential("example.com", "doe", "_", "_")
    #     fullname = pysswords.db.credential.asfullname(
    #         credential.login,
    #         credential.name
    #     )
    #     args = ["-D", "/tmp/pysswords", "--get", fullname]
    #     with patch("pysswords.__main__.Interface") as mocked:
    #         pysswords.__main__.main(args)
    #         self.assertTrue(mocked().get_credential.called)
    #         mocked().get_credential.assert_called_once_with(
    #             name=credential.name,
    #             login=credential.login
    #         )

    # @timethis
    # def test_split_name_returns_name_login_from_name(self):
    #     cred_name = "example.org"
    #     cred_login = "john.doe"
    #     credential_full_name = "{}@{}".format(cred_login, cred_name)
    #     name, login = pysswords.__main__.split_name(credential_full_name)
    #     self.assertEqual(cred_name, name)
    #     self.assertEqual(cred_login, login)

    # @timethis
    # def test_split_name_raises_value_error_when_not_valid_name_given(self):
    #     invalid_name = ""
    #     with self.assertRaises(ValueError):
    #         pysswords.__main__.split_name(invalid_name)

    # @timethis
    # def test_split_name_returns_login_none_when_not_loginname_passed(self):
    #     cred_name = "@example.org"
    #     name, login = pysswords.__main__.split_name(cred_name)
    #     self.assertEqual(cred_name.strip("@"), name)
    #     self.assertEqual(None, login)

    # @timethis
    # def test_print_credentials(self):
    #     credentials = [
    #         some_credential()
    #     ]
    #     with patch('sys.stdout', new_callable=StringIO) as mock_stdout:
    #         pysswords.__main__.print_credentials(credentials)
    #         output = mock_stdout.getvalue()
    #     for credential in credentials:
    #         self.assertIn(credential.name, output)
    #         self.assertIn(credential.login, output)
    #         self.assertIn("***", output)
    #         self.assertIn(credential.comment, output)

    # @timethis
    # def test_decrypt_credentials_is_called_for_every_credential(self):
    #     credentials = [
    #         some_credential(),
    #         some_credential(name="something"),
    #     ]
    #     with patch('pysswords.__main__.Database') as mocked:
    #         pysswords.__main__.decrypt_credentials(
    #             mocked,
    #             credentials=credentials,
    #             passphrase=Mock()
    #         )
    #         self.assertEqual(2, mocked.decrypt.call_count)

    # @timethis
    # def test_with_arg_show_password_asks_for_passphrase(self):
    #     args = ["-D", "/tmp/pysswords", "--show-password"]
    #     with patch("pysswords.__main__.getpass") as mocked_getpass:
    #         with patch("pysswords.__main__.Database"):
    #             pysswords.__main__.main(args)
    #             self.assertTrue(mocked_getpass.called)

    # @timethis
    # def test_with_arg_show_password_checks_for_passphrase(self):
    #     args = ["-D", "/tmp/pysswords", "--show-password"]
    #     passphrase = "dummy"
    #     with patch("pysswords.__main__.getpass") as mocked_getpass:
    #         mocked_getpass.return_value = passphrase
    #         with patch("pysswords.__main__.Database") as mocked_db:
    #             pysswords.__main__.main(args)
    #             mocked_db().check.assert_called_once_with(
    #                 passphrase
    #             )

    # @timethis
    # def test_print_credentials_when_no_arg_is_passed(self):
    #     args = []
    #     with patch("pysswords.__main__.print_credentials") as mocked:
    #         with patch("pysswords.__main__.Database") as mocked_db:
    #             pysswords.__main__.main(args)
    #             mocked.assert_called_once_with(mocked_db().credentials)

    # @timethis
    # def test_update_credential_when_update_arg_passed(self):
    #     credential_name = "example.com"
    #     args = ["-D", "/tmp/pysswords", "--update", credential_name]
    #     with patch("pysswords.__main__.Database") as mocked:
    #         with patch("pysswords.__main__.Interface") as mocked_interface:
    #             mocked().credential.return_value = [some_credential()]
    #             pysswords.__main__.main(args)
    #             self.assertTrue(mocked().update.called)


if __name__ == "__main__":
    if sys.version_info >= (3,):
        unittest.main(warnings=False)
    else:
        unittest.main()
