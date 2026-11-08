"""Имитация объектной модели Word для проверки логики «Продолжения листинга» без Word.

Каждый абзац — одна строка; на странице LINES строк. Позиция в документе = индекс абзаца.
Поведение, важное для багов: скрытые закладки (на «_») не видны без ShowHidden; присваивание Text
диапазону абзаца со знаком абзаца склеивает его со следующим.
"""

LINES = 5


class Obj:
    pass


class Para:
    def __init__(self, doc, text, code=False):
        self.doc, self.text, self.code = doc, text, code
        self.bordered = code
        self.font = "Consolas" if code else "Times New Roman"
        self.Borders = _Borders(self)
        self.Format = Obj()

    @property
    def index(self):
        return self.doc.paras.index(self)

    @property
    def Range(self):
        return Rng(self.doc, self.index, self.index + 1)

    def Previous(self):
        i = self.index
        return self.doc.paras[i - 1] if i > 0 else None


class _Borders:
    def __init__(self, par):
        self.par = par

    @property
    def Enable(self):
        return self.par.bordered

    @Enable.setter
    def Enable(self, v):
        self.par.bordered = bool(v)


class _Font:
    def __init__(self, rng):
        self.rng = rng

    def __setattr__(self, k, v):
        if k == "Name":
            for p in self.rng.paras():
                p.font = v
        object.__setattr__(self, k, v)


class Collection:
    def __init__(self, items):
        self.items = items

    @property
    def Count(self):
        return len(self.items)

    def __call__(self, i):
        return self.items[i - 1]


class Rng:
    def __init__(self, doc, start, end, exclude_mark=False):
        self.doc, self.Start, self.End, self.exclude_mark = doc, start, end, exclude_mark
        self.Document = doc

    def paras(self):
        return self.doc.paras[self.Start:max(self.End, self.Start + 1)]

    @property
    def Paragraphs(self):
        return Collection(self.paras())

    @property
    def Font(self):
        return _Font(self)

    @property
    def Text(self):
        return "".join(p.text + "\r" for p in self.doc.paras[self.Start:self.End])

    @Text.setter
    def Text(self, value):
        p = self.doc.paras[self.Start]
        if self.exclude_mark:
            p.text = value
            return
        # знак абзаца заменён — абзац склеивается со следующим
        nxt = self.doc.paras[self.Start + 1]
        nxt.text = value + nxt.text
        self.doc.paras.remove(p)

    def Information(self, what):
        assert what == 3
        return self.Start // LINES + 1

    def MoveEnd(self, unit, count):
        assert (unit, count) == (1, -1)
        self.exclude_mark = True

    def InsertBefore(self, text):
        assert self.exclude_mark, "вставка с захватом знака абзаца склеит строки"
        p = self.doc.paras[self.Start]
        p.text = text + p.text

    def InsertParagraphBefore(self):
        src = self.doc.paras[self.Start]
        new = Para(self.doc, "", code=src.code)
        self.doc.paras.insert(self.Start, new)

    def Delete(self):
        del self.doc.paras[self.Start:self.End]


class Bookmarks:
    def __init__(self, doc):
        self.doc = doc
        self.marks = {}
        self.ShowHidden = False

    def _visible(self):
        return [n for n in self.marks if self.ShowHidden or not n.startswith("_")]

    @property
    def Count(self):
        return len(self._visible())

    def __call__(self, key):
        name = self._visible()[key - 1] if isinstance(key, int) else key
        first, last = self.marks[name]
        b = Obj()
        b.Name = name
        b.Range = Rng(self.doc, first.index, last.index + 1)
        return b


class FakeDoc:
    def __init__(self):
        self.paras = []
        self.Bookmarks = Bookmarks(self)
        self.repaginations = 0

    def add(self, text, code=False):
        p = Para(self, text, code)
        self.paras.append(p)
        return p

    def Range(self, a, b):
        return Rng(self, a, b)

    def Repaginate(self):
        self.repaginations += 1
