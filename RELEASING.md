Releasing clld
==============

- Do platform test via tox (making sure statement coverage is at 100%):
```
tox -r
```

- Make sure all scaffold tests pass (Py 2.7, 3.4): After pushing the develop branch run
```
./venvs/clld/clld/build.sh "<prev-rel-no>"
```

- Make sure javascript tests pass with coverage of clld.js at > 82%::
```
java -jar tools/jsTestDriver/JsTestDriver-1.3.5.jar --tests all --browser chromium-browser --port 9877
```

- Make sure flake8 passes::
```
flake8 --ignore=E711,E712,D100,D101,D103,D102,D301,E402,E731,W503 --max-line-length=100 clld
```

- Make sure docs render OK::
```
cd docs
make clean html
cd ..
```

- Update translations (using a py3 env with babel patched for py3 compatibility).
```
python setup.py compile_catalog
```

- Start a release
```
git flow release start <release number>
```

- Change clld/__init__.py to the new version number.

- Change setup.py version to the new version number.

- Change docs/conf.py version to the new version number.

- Change CHANGES.rst heading to reflect the new version number.

- Bump version number:
```
git commit -a -m"bumped version number"
```

- Create a release tag:
```
git flow release finish <release number>
```

- Push to github:
```
git push origin develop
git checkout master
git push origin master
git checkout develop
git push --tags
```

- Re-release using the same tag on GitHub, to trigger the ZENODO hook.
  update the DOI badge (later?)

- Make sure your system Python has ``setuptools-git`` installed and release to
  PyPI:
```
./pypi.sh <release number>
```

- Make sure the new version is installed locally:
```
python setup.py develop
```
