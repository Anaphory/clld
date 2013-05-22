from six import PY3
if not PY3:
    import xlwt
else:
    xlwt = None


def hyperlink(url, label=None):
    f = xlwt.Font()
    f.underline = xlwt.Font.UNDERLINE_SINGLE

    style = xlwt.XFStyle()
    style.font = f

    return xlwt.Formula('HYPERLINK("%s";"%s")' % (
        url, label.replace('"', "'") if label else url))