"""Оценка этапа «Проект» на живой модели: набор настоящих лаб (языки и виды программ) → план проекта → проверка →
сборка с исправлениями (до трёх кругов) → проверка на опасное → запуск по сценарию снимка → сверка листингов →
поиск «следов ИИ». Итог — JSON-отчёт и проекты в папке --out (не в репозитории).

    python tools/eval_projects.py --out DIR [--only id,id] [--reuse] [--workers 3]

Модель — из переменных окружения FORLABS_BASE_URL, FORLABS_MODEL, FORLABS_API_KEY.

Что проверяется здесь, в Linux:
- консольные программы (C++, Python, Java, Node, PHP, Go, C# — если есть dotnet) запускаются в псевдотерминале,
  как в окне консоли Windows: ввод печатается построчно и отображается на экране, программа, ждущая ввода, не
  получает конец файла; у Go/PHP/Node/Java строка ввода кончается на \\r\\n (так их читает консоль Windows);
  C# и Java — с русской культурой (дробные — с запятой); ожидаемый текст ищется на «экране»;
- лишний ввод (программа уже завершилась, а строки остались) — в Windows его съест «pause» и окно закроется;
- tkinter (python3 с tkinter) и Swing — в Xvfb, шаги type/key — через xdotool, текст окна читает «зонд» внутри
  программы; проверяется, что ввод попал в поля и в окне появился ожидаемый текст;
- WinForms/WPF — имена элементов из шагов есть в коде (Name / x:Name), обработчики нажатий подключены;
- сайт — файлы страниц есть, скрипты проходят node --check; сайт на PHP — страница по адресу снимка отдаётся
  встроенным сервером без ошибок PHP;
- MySQL — если есть сервер (mysql/mariadb), ввод снимка выполняется клиентом на чистом сервере.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import pty
import re
import select
import shutil
import signal
import socket
import struct
import subprocess
import sys
import termios
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from textwrap import dedent

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from forlabs.core.models import (  # noqa: E402
    DashList,
    Figure,
    LabSpec,
    LabState,
    Listing,
    Material,
    Paragraph,
    ReportDoc,
    Task,
    TaskSection,
)
from forlabs.pipeline import project as proj  # noqa: E402
from forlabs.runner import languages, safety  # noqa: E402
from forlabs.runner.plan import DATA_EXT, ProjectPlan, validate  # noqa: E402


# ---------- набор лаб ----------

def P(text: str) -> dict:
    return {"type": "paragraph", "text": text}


def D(*items: str) -> dict:
    return {"type": "dash_list", "items": list(items)}


def L(topic: str, language: str, code: str) -> dict:
    return {"type": "listing", "topic": topic, "language": language, "code": dedent(code).strip("\n")}


def F(topic: str, slot: str) -> dict:
    return {"type": "figure", "topic": topic, "slot": slot}


LABS: list[dict] = []


def lab(**kw) -> None:
    LABS.append(kw)


# --- C#: консоль (классы) ---
lab(id="cs_console", lang="csharp", kind="console", subject="kpiyap", number="3",
    topic="Классы и объекты в C#", goal="Изучить описание классов, полей, конструкторов и методов в C#",
    tasks=["Описать класс Product (товар) с полями название, цена и количество, конструктором и методами GetCost и "
           "Display",
           "Согласно варианту 7 разработать программу, которая вводит с клавиатуры данные трёх товаров и выводит их "
           "список и общую стоимость"],
    sections=[
        [P("Согласно варианту 7 разработан класс Product, описывающий товар на складе магазина:"),
         D("- поля name, price и count хранят название, цену и количество товара;",
           "- конструктор заполняет все поля при создании объекта;",
           "- метод GetCost возвращает стоимость партии товара;",
           "- метод Display выводит сведения о товаре в одну строку.")],
        [P("Согласно варианту 7 разработана программа на языке C#, реализующая учёт товаров:"),
         D("- в цикле вводятся название, цена и количество трёх товаров с проверкой ввода через TryParse;",
           "- объекты Product сохраняются в массив products;",
           "- после ввода выводится список товаров и общая стоимость."),
         L("Исходный код программы", "csharp", r'''
            using System;

            namespace Lab3
            {
                class Product
                {
                    public string name;
                    public double price;
                    public int count;

                    public Product(string name, double price, int count)
                    {
                        this.name = name;
                        this.price = price;
                        this.count = count;
                    }

                    public double GetCost()
                    {
                        return price * count;
                    }

                    public void Display()
                    {
                        Console.WriteLine("Товар: " + name + ", цена: " + price +
                            " руб., количество: " + count + " шт.");
                    }
                }

                class Program
                {
                    static void Main(string[] args)
                    {
                        Product[] products = new Product[3];
                        double total = 0;

                        for (int i = 0; i < products.Length; i++)
                        {
                            Console.Write("Введите название товара " + (i + 1) + ": ");
                            string name = Console.ReadLine();
                            double price;
                            Console.Write("Введите цену: ");
                            while (!double.TryParse(Console.ReadLine(), out price))
                            {
                                Console.Write("Введите число: ");
                            }
                            int count;
                            Console.Write("Введите количество: ");
                            while (!int.TryParse(Console.ReadLine(), out count))
                            {
                                Console.Write("Введите число: ");
                            }
                            products[i] = new Product(name, price, count);
                        }

                        Console.WriteLine("Список товаров:");
                        for (int i = 0; i < products.Length; i++)
                        {
                            products[i].Display();
                            total += products[i].GetCost();
                        }
                        Console.WriteLine("Общая стоимость: " + total + " руб.");
                    }
                }
            }
            '''),
         F("Результат выполнения программы учёта товаров",
           "Открой терминал в папке «Индивидуальное задание», выполни dotnet run, введи данные трёх товаров по "
           "подсказкам; в кадре весь вывод программы со списком товаров и общей стоимостью")]])

# --- C#: WinForms (текст написан настоящим писателем отчётов) ---
lab(id="cs_winforms", lang="csharp", kind="winforms", subject="trpo", number="5",
    topic="Разработка оконного приложения Windows Forms",
    goal="Научиться создавать оконные приложения на C# с элементами TextBox, ComboBox, Button и окнами сообщений",
    tasks=["Создать проект Windows Forms «Калькулятор», разместить на форме два поля ввода чисел, выпадающий список "
           "операций (+, -, *, /) и кнопку «Вычислить»",
           "Написать обработчик кнопки: результат выводится в окне MessageBox; при делении на ноль — сообщение об "
           "ошибке. Проверить работу программы"],
    sections=[
        [P("В Visual Studio создан проект Windows Forms App с именем Calculator. На форме Form1 размещены элементы:"),
         D("- два элемента TextBox с именами textBox1 и textBox2 для ввода чисел;",
           "- ComboBox с именем comboBox1, в список которого добавлены операции +, -, *, /;",
           "- Button с именем button1 и надписью «Вычислить»."),
         F("Форма Form1 с элементами управления в конструкторе Visual Studio",
           "Открой проект Calculator в Visual Studio → открой Form1.cs [Конструктор]; в кадре должны быть видны два "
           "поля ввода, выпадающий список с операциями и кнопка «Вычислить».")],
        [P("В обработчике button1_Click прочитал оба числа через double.Parse, определил выбранную операцию по "
           "comboBox1.Text и вывел результат через MessageBox.Show. При делении на ноль выводится сообщение «Деление "
           "на ноль невозможно»."),
         L("Обработчик нажатия кнопки «Вычислить» в файле Form1.cs", "csharp", r'''
            using System;
            using System.Windows.Forms;

            namespace Calculator
            {
                public partial class Form1 : Form
                {
                    public Form1()
                    {
                        InitializeComponent();
                    }

                    private void button1_Click(object sender, EventArgs e)
                    {
                        double a = double.Parse(textBox1.Text);
                        double b = double.Parse(textBox2.Text);
                        double result = 0;
                        string op = comboBox1.Text;

                        if (op == "+")
                        {
                            result = a + b;
                        }
                        else if (op == "-")
                        {
                            result = a - b;
                        }
                        else if (op == "*")
                        {
                            result = a * b;
                        }
                        else if (op == "/")
                        {
                            if (b == 0)
                            {
                                MessageBox.Show("Деление на ноль невозможно");
                                return;
                            }
                            result = a / b;
                        }

                        MessageBox.Show("Результат: " + result);
                    }
                }
            }
            '''),
         F("Результат сложения двух чисел в окне MessageBox",
           "Запусти программу, введи в первое поле 12, во второе 8, выбери операцию + и нажми «Вычислить»; в кадре "
           "должно быть окно сообщения с результатом 20."),
         F("Сообщение об ошибке при делении на ноль",
           "Введи в первое поле 5, во второе 0, выбери операцию / и нажми «Вычислить»; в кадре должно быть окно с "
           "текстом «Деление на ноль невозможно».")]])

# --- C#: WPF (список книг) ---
lab(id="cs_wpf", lang="csharp", kind="wpf", subject="trpo", number="6", topic="Приложение WPF для учёта книг",
    goal="Научиться создавать окна WPF в разметке XAML и обрабатывать события элементов управления",
    tasks=["Создать в Visual Studio проект WPF «Библиотека» и описать класс Book",
           "Разметить главное окно: список книг ListBox, поля для названия, автора и года, кнопки «Добавить» и "
           "«Удалить»",
           "Написать обработчики кнопок и проверить работу программы"],
    sections=[
        [P("В Visual Studio создан проект WPF App с именем Library. Для хранения сведений о книге добавлен класс Book "
           "со свойствами Title, Author и Year и конструктором, который их заполняет."),
         L("Исходный код класса Book", "csharp", r'''
            namespace Library
            {
                public class Book
                {
                    public string Title { get; set; }
                    public string Author { get; set; }
                    public int Year { get; set; }

                    public Book(string title, string author, int year)
                    {
                        Title = title;
                        Author = author;
                        Year = year;
                    }
                }
            }
            '''),
         F("Проект Library в обозревателе решений Visual Studio",
           "Открой проект Library в Visual Studio; в кадре обозреватель решений с файлами App.xaml, MainWindow.xaml и "
           "Book.cs")],
        [P("Окно MainWindow разделено на две колонки: слева список книг lstBooks, справа поля ввода txtTitle, "
           "txtAuthor, txtYear, кнопки btnAdd и btnDelete и надпись txtCount с количеством книг."),
         L("Разметка главного окна MainWindow.xaml", "xml", r'''
            <Window x:Class="Library.MainWindow"
                    xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
                    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
                    Title="Библиотека" Height="400" Width="560">
                <Grid Margin="10">
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="*"/>
                        <ColumnDefinition Width="200"/>
                    </Grid.ColumnDefinitions>
                    <ListBox x:Name="lstBooks" Grid.Column="0" Margin="0,0,10,0"/>
                    <StackPanel Grid.Column="1">
                        <TextBlock Text="Название:"/>
                        <TextBox x:Name="txtTitle" Margin="0,2,0,8"/>
                        <TextBlock Text="Автор:"/>
                        <TextBox x:Name="txtAuthor" Margin="0,2,0,8"/>
                        <TextBlock Text="Год издания:"/>
                        <TextBox x:Name="txtYear" Margin="0,2,0,8"/>
                        <Button x:Name="btnAdd" Content="Добавить" Click="btnAdd_Click"
                                Margin="0,4,0,4"/>
                        <Button x:Name="btnDelete" Content="Удалить"
                                Click="btnDelete_Click"/>
                        <TextBlock x:Name="txtCount" Margin="0,10,0,0"/>
                    </StackPanel>
                </Grid>
            </Window>
            '''),
         F("Разметка главного окна в редакторе XAML",
           "Открой MainWindow.xaml в Visual Studio; в кадре редактор разметки и окно конструктора")],
        [P("В конструкторе окна список заполняется тремя книгами. Обработчик btnAdd_Click проверяет поля и добавляет "
           "книгу, btnDelete_Click удаляет выбранную книгу, метод ShowBooks обновляет список и количество."),
         L("Исходный код окна MainWindow.xaml.cs", "csharp", r'''
            using System.Collections.Generic;
            using System.Windows;

            namespace Library
            {
                public partial class MainWindow : Window
                {
                    List<Book> books = new List<Book>();

                    public MainWindow()
                    {
                        InitializeComponent();
                        books.Add(new Book("Мастер и Маргарита", "М. А. Булгаков", 1967));
                        books.Add(new Book("Тихий Дон", "М. А. Шолохов", 1940));
                        books.Add(new Book("Белая гвардия", "М. А. Булгаков", 1925));
                        ShowBooks();
                    }

                    void ShowBooks()
                    {
                        lstBooks.Items.Clear();
                        foreach (Book b in books)
                        {
                            lstBooks.Items.Add(b.Title + " — " + b.Author + ", " + b.Year);
                        }
                        txtCount.Text = "Всего книг: " + books.Count;
                    }

                    private void btnAdd_Click(object sender, RoutedEventArgs e)
                    {
                        int year;
                        if (txtTitle.Text == "" || txtAuthor.Text == "" ||
                            !int.TryParse(txtYear.Text, out year))
                        {
                            MessageBox.Show("Заполните все поля", "Ошибка");
                            return;
                        }
                        books.Add(new Book(txtTitle.Text, txtAuthor.Text, year));
                        ShowBooks();
                        txtTitle.Text = "";
                        txtAuthor.Text = "";
                        txtYear.Text = "";
                    }

                    private void btnDelete_Click(object sender, RoutedEventArgs e)
                    {
                        if (lstBooks.SelectedIndex >= 0)
                        {
                            books.RemoveAt(lstBooks.SelectedIndex);
                            ShowBooks();
                        }
                    }
                }
            }
            '''),
         F("Окно программы после добавления книги",
           "Запусти программу, введи название «Война и мир», автора «Л. Н. Толстой», год 1869 и нажми «Добавить»; в "
           "кадре список из четырёх книг и надпись «Всего книг: 4»"),
         F("Сообщение о незаполненных полях",
           "Запусти программу и нажми «Добавить», не заполняя поля; в кадре окно сообщения «Заполните все поля»")]])

# --- C++: массивы и функции (второе задание — только новый код, как пишет писатель) ---
lab(id="cpp_console", lang="cpp", kind="console", subject="kpiyap", number="2",
    topic="Одномерные массивы и функции в C++",
    goal="Научиться передавать массивы в функции и обрабатывать их элементы",
    tasks=["Написать функции ввода и вывода массива, поиска максимального элемента и суммы отрицательных элементов",
           "Добавить функцию сортировки массива по возрастанию методом пузырька и вывести отсортированный массив"],
    sections=[
        [P("Программа работает с массивом целых чисел размером до 100 элементов:"),
         D("- функция inputArray вводит элементы с клавиатуры;", "- функция printArray выводит массив в строку;",
           "- функция findMax возвращает максимальный элемент;",
           "- функция sumNegative считает сумму отрицательных элементов."),
         L("Исходный код программы", "cpp", r'''
            #include <iostream>
            using namespace std;

            const int MAX_SIZE = 100;

            void inputArray(int arr[], int n)
            {
                for (int i = 0; i < n; i++)
                {
                    cout << "Введите элемент " << i + 1 << ": ";
                    cin >> arr[i];
                }
            }

            void printArray(int arr[], int n)
            {
                for (int i = 0; i < n; i++)
                {
                    cout << arr[i] << " ";
                }
                cout << endl;
            }

            int findMax(int arr[], int n)
            {
                int max = arr[0];
                for (int i = 1; i < n; i++)
                {
                    if (arr[i] > max)
                    {
                        max = arr[i];
                    }
                }
                return max;
            }

            int sumNegative(int arr[], int n)
            {
                int sum = 0;
                for (int i = 0; i < n; i++)
                {
                    if (arr[i] < 0)
                    {
                        sum += arr[i];
                    }
                }
                return sum;
            }

            int main()
            {
                int arr[MAX_SIZE];
                int n;
                cout << "Введите количество элементов массива: ";
                cin >> n;
                inputArray(arr, n);
                cout << "Исходный массив: ";
                printArray(arr, n);
                cout << "Максимальный элемент: " << findMax(arr, n) << endl;
                cout << "Сумма отрицательных элементов: " << sumNegative(arr, n) << endl;
                return 0;
            }
            '''),
         F("Результат работы программы", "Запусти программу, введи 6 элементов: 5, -3, 12, 0, -7, 8; в кадре исходный "
                                         "массив, максимальный элемент и сумма отрицательных элементов")],
        [P("Добавлена функция sortArray, которая сортирует массив методом пузырька: соседние элементы меняются "
           "местами, пока больший стоит раньше меньшего. В функции main после вывода суммы отрицательных элементов "
           "добавлен вызов sortArray и вывод отсортированного массива с подписью «Отсортированный массив: »."),
         L("Функция сортировки массива методом пузырька", "cpp", r'''
            void sortArray(int arr[], int n)
            {
                for (int i = 0; i < n - 1; i++)
                {
                    for (int j = 0; j < n - i - 1; j++)
                    {
                        if (arr[j] > arr[j + 1])
                        {
                            int temp = arr[j];
                            arr[j] = arr[j + 1];
                            arr[j + 1] = temp;
                        }
                    }
                }
            }
            '''),
         F("Отсортированный массив", "Запусти программу с теми же данными; в кадре отсортированный массив "
                                     "-7 -3 0 5 8 12")]])

# --- C++: структуры и файлы ---
lab(id="cpp_struct_file", lang="cpp", kind="console", subject="kpiyap", number="6",
    topic="Структуры и работа с текстовыми файлами в C++",
    goal="Научиться описывать структуры и читать и записывать текстовые файлы с помощью потоков fstream",
    tasks=["Описать структуру Student и подготовить файл data.txt со сведениями о студентах группы",
           "Прочитать данные из файла, вывести студентов со средним баллом не ниже введённого и записать их в файл "
           "result.txt"],
    sections=[
        [P("Структура Student хранит фамилию, группу и средний балл студента. Исходные данные записаны в файл data.txt "
           "рядом с программой, по одному студенту в строке через пробел."),
         L("Содержимое файла data.txt", "text", '''
            Ковалёв РС02-23 7.8
            Мельник РС02-23 8.6
            Савченко РС02-23 6.9
            Гончаренко РС02-23 9.1
            Литвин РС02-23 8.0
            ''')],
        [P("Программа читает файл data.txt в массив структур, выводит весь список, запрашивает минимальный средний "
           "балл и записывает подходящих студентов в файл result.txt:"),
         D("- чтение выполняется потоком ifstream до конца файла;", "- запись — потоком ofstream;",
           "- количество найденных студентов выводится в конце."),
         L("Исходный код программы", "cpp", r'''
            #include <iostream>
            #include <fstream>
            #include <string>
            using namespace std;

            struct Student
            {
                string surname;
                string group;
                double average;
            };

            int main()
            {
                Student students[50];
                int count = 0;
                ifstream fin("data.txt");
                if (!fin)
                {
                    cout << "Не удалось открыть файл data.txt" << endl;
                    return 1;
                }
                while (count < 50 && fin >> students[count].surname
                       >> students[count].group >> students[count].average)
                {
                    count++;
                }
                fin.close();

                cout << "Список студентов:" << endl;
                for (int i = 0; i < count; i++)
                {
                    cout << students[i].surname << " " << students[i].group << " "
                         << students[i].average << endl;
                }

                double limit;
                cout << "Введите минимальный средний балл: ";
                cin >> limit;

                ofstream fout("result.txt");
                int found = 0;
                cout << "Студенты со средним баллом не ниже " << limit << ":" << endl;
                for (int i = 0; i < count; i++)
                {
                    if (students[i].average >= limit)
                    {
                        cout << students[i].surname << " " << students[i].average << endl;
                        fout << students[i].surname << " " << students[i].group << " "
                             << students[i].average << endl;
                        found++;
                    }
                }
                fout.close();
                cout << "Найдено студентов: " << found << endl;
                cout << "Результат записан в файл result.txt" << endl;
                return 0;
            }
            '''),
         F("Результат работы программы", "Запусти программу, введи минимальный балл 8; в кадре список студентов и "
                                         "студенты со средним баллом не ниже 8"),
         F("Содержимое файла result.txt", "Открой result.txt в Блокноте; в кадре три строки с фамилиями студентов")]])

# --- Python: списки и словари (второе задание — только новый код) ---
lab(id="py_console", lang="python", kind="console", subject="trpo", number="2",
    topic="Списки и словари в Python", goal="Научиться обрабатывать данные с помощью списков и словарей Python",
    tasks=["Написать программу «Журнал оценок»: ввести фамилии и оценки студентов в словарь, вывести журнал по "
           "алфавиту, средний балл и лучшего студента",
           "Добавить поиск оценки студента по фамилии"],
    sections=[
        [P("Программа хранит оценки в словаре students, где ключ — фамилия, значение — оценка:"),
         D("- фамилия и оценка вводятся в одной строке через пробел;",
           "- журнал выводится по алфавиту с помощью sorted;",
           "- средний балл считается по списку значений словаря;",
           "- лучший студент находится функцией max с параметром key."),
         L("Исходный код программы", "python", r'''
            students = {}
            n = int(input("Введите количество студентов: "))
            for i in range(n):
                data = input("Введите фамилию и оценку через пробел: ").split()
                students[data[0]] = int(data[1])

            print("Журнал оценок:")
            for name in sorted(students):
                print(name, "-", students[name])

            grades = list(students.values())
            average = sum(grades) / len(grades)
            print("Средний балл группы:", round(average, 2))

            best = max(students, key=students.get)
            print("Лучший студент:", best, "с оценкой", students[best])

            good = [name for name in students if students[name] >= 8]
            print("Оценку 8 и выше получили:", ", ".join(good))
            '''),
         F("Результат работы программы «Журнал оценок»",
           "Запусти программу, введи 4 студентов: Петренко 7, Абрамович 9, Сидорук 8, Белова 6; в кадре журнал, "
           "средний балл и лучший студент")],
        [P("В конец программы добавлен поиск: фамилия вводится с клавиатуры и проверяется оператором in."),
         L("Поиск оценки студента по фамилии", "python", r'''
            surname = input("Введите фамилию для поиска: ")
            if surname in students:
                print("Оценка студента", surname + ":", students[surname])
            else:
                print("Студент", surname, "не найден")
            '''),
         F("Результат поиска оценки студента", "Запусти программу с теми же данными и введи для поиска фамилию "
                                               "Сидорук; в кадре строка «Оценка студента Сидорук: 8»")]])

# --- Python: tkinter (текст написан настоящим писателем отчётов) ---
lab(id="py_tkinter", lang="python", kind="gui", subject="trpo", number="3",
    topic="Графический интерфейс на Python с библиотекой tkinter",
    goal="Изучить создание оконных приложений на Python с помощью библиотеки tkinter",
    tasks=["Создать окно программы «Индекс массы тела» с двумя полями ввода (рост в сантиметрах и вес в килограммах), "
           "кнопкой «Рассчитать» и надписью для результата",
           "Проверить работу программы на данных: рост 180 см, вес 75 кг"],
    sections=[
        [P("Создан файл imt.py с окном 400x250 и заголовком «Индекс массы тела». В окне размещены два поля ввода (рост "
           "в сантиметрах и вес в килограммах), кнопка «Рассчитать» и метка для вывода результата."),
         L("Исходный код программы расчёта индекса массы тела", "python", r'''
            import tkinter as tk
            from tkinter import messagebox


            def calculate():
                try:
                    height = float(height_entry.get()) / 100
                    weight = float(weight_entry.get())
                    bmi = weight / (height ** 2)
                    result_label.config(text=f"ИМТ: {bmi:.1f}")
                except ValueError:
                    messagebox.showerror("Ошибка", "Введите числовые значения роста и веса")


            root = tk.Tk()
            root.title("Индекс массы тела")
            root.geometry("400x250")

            tk.Label(root, text="Рост (см):").pack()
            height_entry = tk.Entry(root)
            height_entry.pack()

            tk.Label(root, text="Вес (кг):").pack()
            weight_entry = tk.Entry(root)
            weight_entry.pack()

            tk.Button(root, text="Рассчитать", command=calculate).pack(pady=10)

            result_label = tk.Label(root, text="ИМТ: ")
            result_label.pack()

            root.mainloop()
            '''),
         F("Окно программы «Индекс массы тела» после расчёта",
           "Запусти программу, введи в поля роста 180, веса 75, нажми «Рассчитать». В кадре должно быть видно окно с "
           "заголовком «Индекс массы тела», поля ввода с числами и метку с результатом «ИМТ: 23.1».")],
        [P("Запустил imt.py, ввёл в поле «Рост (см):» значение 180, в поле «Вес (кг):» — 75 и нажал «Рассчитать»."),
         P("Рост переводится в метры делением на 100, поэтому ИМТ = 75 / 1.8² ≈ 23,1, метка выводит «ИМТ: 23.1»."),
         F("Окно программы «Индекс массы тела» с результатом для роста 180 см и веса 75 кг",
           "Запусти imt.py → введи рост 180 и вес 75 → нажми «Рассчитать»; в кадре окно 400x250 с заполненными полями "
           "и меткой «ИМТ: 23.1».")]])

# --- Python: файл CSV от преподавателя ---
SALES_CSV = """Дата;Товар;Категория;Количество;Цена
01.10.2026;Молоко 3,2%;Молочные продукты;14;2,35
01.10.2026;Хлеб «Бородинский»;Хлебобулочные изделия;20;1,89
02.10.2026;Сыр «Российский»;Молочные продукты;6;12,40
02.10.2026;Батон нарезной;Хлебобулочные изделия;18;1,45
03.10.2026;Яблоки;Фрукты;25;3,10
03.10.2026;Кефир 2,5%;Молочные продукты;9;2,05
04.10.2026;Бананы;Фрукты;12;4,60
04.10.2026;Молоко 3,2%;Молочные продукты;10;2,35
"""
lab(id="py_csv", lang="python", kind="console", subject="trpo", number="7", topic="Обработка CSV-файлов в Python",
    goal="Научиться читать и обрабатывать табличные данные из CSV-файлов модулем csv",
    tasks=["Прочитать файл продаж sales.csv, полученный от преподавателя, и посчитать выручку по категориям товаров",
           "Найти самый прибыльный товар и сохранить выручку по категориям в файл report.txt"],
    materials=[("sales.csv", SALES_CSV)],
    sections=[
        [P("Файл sales.csv содержит продажи магазина за неделю: дату, товар, категорию, количество и цену, разделитель "
           "— точка с запятой. Программа читает его через csv.DictReader и суммирует выручку в словаре revenue по "
           "категориям; цена записана с запятой, поэтому перед преобразованием в число запятая заменяется точкой."),
         L("Исходный код программы обработки файла sales.csv", "python", r'''
            import csv

            revenue = {}
            products = {}

            with open("sales.csv", encoding="utf-8") as f:
                reader = csv.DictReader(f, delimiter=";")
                for row in reader:
                    count = int(row["Количество"])
                    price = float(row["Цена"].replace(",", "."))
                    total = count * price
                    category = row["Категория"]
                    revenue[category] = revenue.get(category, 0) + total
                    products[row["Товар"]] = products.get(row["Товар"], 0) + total

            print("Выручка по категориям:")
            for category, total in revenue.items():
                print(f"{category}: {total:.2f} руб.")

            best = max(products, key=products.get)
            print(f"Самый прибыльный товар: {best} ({products[best]:.2f} руб.)")

            with open("report.txt", "w", encoding="utf-8") as f:
                for category, total in revenue.items():
                    f.write(f"{category};{total:.2f}\n")
            print("Отчёт сохранён в файл report.txt")
            '''),
         F("Результат обработки файла продаж", "Запусти программу в папке с файлом sales.csv; в кадре выручка по "
                                               "категориям и самый прибыльный товар")],
        [P("Самый прибыльный товар определяется по словарю products функцией max. Выручка по категориям записывается в "
           "файл report.txt в виде «категория;сумма»."),
         F("Содержимое файла report.txt", "Открой report.txt в Блокноте; в кадре три строки с категориями и суммами")]])

# --- Java: классы и наследование ---
lab(id="java_console", lang="java", kind="console", subject="ssp", number="5",
    topic="Классы и иерархии классов в Java",
    goal="Изучить наследование классов, вызов конструктора базового класса и переопределение методов в Java",
    tasks=["Описать базовый класс Transport и производные классы Car и Truck",
           "Написать главный класс Main: создать массив транспортных средств, ввести расстояние и вывести время в пути "
           "для каждого"],
    sections=[
        [P("Согласно варианту 4 из приложения А разработана иерархия классов транспорта:"),
         D("- базовый класс Transport хранит название и скорость, метод getTime считает время в пути;",
           "- класс Car добавляет количество пассажиров;", "- класс Truck добавляет грузоподъёмность;",
           "- метод getInfo переопределён в обоих производных классах."),
         L("Исходный код класса Transport", "java", r'''
            public class Transport {
                protected String name;
                protected int speed;

                public Transport(String name, int speed) {
                    this.name = name;
                    this.speed = speed;
                }

                public double getTime(double distance) {
                    return distance / speed;
                }

                public String getInfo() {
                    return name + ", скорость " + speed + " км/ч";
                }
            }
            '''),
         L("Исходный код класса Car", "java", r'''
            public class Car extends Transport {
                private int passengers;

                public Car(String name, int speed, int passengers) {
                    super(name, speed);
                    this.passengers = passengers;
                }

                @Override
                public String getInfo() {
                    return "Автомобиль " + super.getInfo() + ", пассажиров: " + passengers;
                }
            }
            '''),
         L("Исходный код класса Truck", "java", r'''
            public class Truck extends Transport {
                private double capacity;

                public Truck(String name, int speed, double capacity) {
                    super(name, speed);
                    this.capacity = capacity;
                }

                @Override
                public String getInfo() {
                    return "Грузовик " + super.getInfo() + ", грузоподъёмность: "
                            + capacity + " т";
                }
            }
            ''')],
        [P("В классе Main создаётся массив из трёх объектов, расстояние вводится с клавиатуры через Scanner, время в "
           "пути выводится с одним знаком после запятой."),
         L("Исходный код класса Main", "java", r'''
            import java.util.Scanner;

            public class Main {
                public static void main(String[] args) {
                    Scanner in = new Scanner(System.in);
                    Transport[] list = {
                        new Car("Лада Веста", 90, 5),
                        new Truck("МАЗ-5440", 70, 20),
                        new Car("Geely Atlas", 100, 5)
                    };
                    System.out.print("Введите расстояние в километрах: ");
                    double distance = in.nextDouble();
                    for (Transport t : list) {
                        System.out.println(t.getInfo());
                        System.out.println(String.format("Время в пути: %.1f ч",
                                t.getTime(distance)));
                    }
                }
            }
            '''),
         F("Результат выполнения программы", "Запусти программу, введи расстояние 180; в кадре сведения о трёх "
                                             "транспортных средствах и время в пути")]])

# --- Java: коллекции; в листинге писатель забыл импорты (сборка должна пройти через исправление) ---
lab(id="java_import", lang="java", kind="console", subject="ssp", number="6", topic="Коллекции ArrayList в Java",
    goal="Научиться хранить и обрабатывать данные в списках ArrayList и сортировать их методами класса Collections",
    tasks=["Создать список городов ArrayList, удалить из него элемент и вывести количество городов",
           "Отсортировать список по алфавиту и вывести города, которые начинаются на введённую букву"],
    sections=[
        [P("Список cities типа ArrayList<String> заполняется методом add, элемент удаляется методом remove по "
           "значению, количество элементов возвращает метод size."),
         L("Исходный код программы Main.java", "java", r'''
            import java.util.Scanner;

            public class Main {
                public static void main(String[] args) {
                    Scanner in = new Scanner(System.in);
                    ArrayList<String> cities = new ArrayList<>();
                    cities.add("Минск");
                    cities.add("Брест");
                    cities.add("Гродно");
                    cities.add("Витебск");
                    cities.add("Гомель");
                    cities.add("Могилёв");
                    System.out.println("Список городов: " + cities);

                    cities.remove("Брест");
                    System.out.println("После удаления: " + cities);
                    System.out.println("Количество городов: " + cities.size());

                    Collections.sort(cities);
                    System.out.println("По алфавиту: " + cities);

                    System.out.print("Введите первую букву: ");
                    String letter = in.nextLine();
                    for (String city : cities) {
                        if (city.startsWith(letter)) {
                            System.out.println(city);
                        }
                    }
                }
            }
            ''')],
        [P("Метод Collections.sort упорядочивает список по алфавиту, затем в цикле for-each выводятся города, у "
           "которых метод startsWith возвращает true для введённой буквы."),
         F("Результат работы программы", "Запусти программу и введи букву Г; в кадре список городов, список по "
                                         "алфавиту и города Гомель и Гродно")]])

# --- Java: Swing ---
lab(id="java_swing", lang="java", kind="gui", subject="ssp", number="7", topic="Графический интерфейс на Swing",
    goal="Научиться создавать окна с полями ввода и кнопками на Swing и обрабатывать нажатия",
    tasks=["Создать окно «Прямоугольник» с полями для длины и ширины, кнопкой «Вычислить» и надписью для результата",
           "Обработать ошибку ввода: если введено не число, показать окно сообщения"],
    sections=[
        [P("Окно RectangleFrame наследует JFrame, элементы расположены менеджером GridLayout в четыре строки:"),
         D("- поля widthField и heightField типа JTextField для длины и ширины;",
           "- кнопка calcButton с надписью «Вычислить»;", "- надпись resultLabel для площади и периметра."),
         L("Исходный код класса RectangleFrame", "java", r'''
            import javax.swing.*;
            import java.awt.*;
            import java.awt.event.ActionEvent;
            import java.awt.event.ActionListener;

            public class RectangleFrame extends JFrame {
                private JTextField widthField;
                private JTextField heightField;
                private JLabel resultLabel;

                public RectangleFrame() {
                    setTitle("Прямоугольник");
                    setSize(360, 220);
                    setDefaultCloseOperation(JFrame.EXIT_ON_CLOSE);
                    setLocationRelativeTo(null);
                    setLayout(new GridLayout(4, 2, 5, 5));

                    add(new JLabel("Длина:"));
                    widthField = new JTextField();
                    add(widthField);

                    add(new JLabel("Ширина:"));
                    heightField = new JTextField();
                    add(heightField);

                    JButton calcButton = new JButton("Вычислить");
                    add(calcButton);
                    resultLabel = new JLabel("");
                    add(resultLabel);

                    calcButton.addActionListener(new ActionListener() {
                        public void actionPerformed(ActionEvent e) {
                            calculate();
                        }
                    });
                }

                private void calculate() {
                    try {
                        double a = Double.parseDouble(widthField.getText());
                        double b = Double.parseDouble(heightField.getText());
                        resultLabel.setText("S = " + (a * b) + ", P = " + 2 * (a + b));
                    } catch (NumberFormatException ex) {
                        JOptionPane.showMessageDialog(this, "Введите числа",
                                "Ошибка", JOptionPane.ERROR_MESSAGE);
                    }
                }

                public static void main(String[] args) {
                    SwingUtilities.invokeLater(new Runnable() {
                        public void run() {
                            new RectangleFrame().setVisible(true);
                        }
                    });
                }
            }
            '''),
         F("Окно программы с результатом расчёта", "Запусти программу, введи длину 6 и ширину 4, нажми «Вычислить»; в "
                                                   "кадре окно с надписью «S = 24.0, P = 20.0»")],
        [P("Если в поле введено не число, Double.parseDouble выбрасывает исключение NumberFormatException, и программа "
           "показывает окно сообщения «Введите числа»."),
         F("Сообщение об ошибке ввода", "Введи в поле длины слово «пять» и нажми «Вычислить»; в кадре окно сообщения "
                                        "«Введите числа»")]])

# --- JavaScript: Node ---
lab(id="js_node", lang="javascript", kind="console", subject="psip", number="8",
    topic="Массивы объектов в JavaScript", goal="Научиться обрабатывать массивы объектов методами forEach, filter и "
                                                "sort и запускать программы в Node.js",
    tasks=["Описать массив покупок и функции подсчёта суммы и вывода списка",
           "Отобрать покупки не дешевле введённой цены и отсортировать их по убыванию цены"],
    sections=[
        [P("Массив products хранит покупки в виде объектов с полями name, price и count:"),
         D("- функция getTotal считает общую стоимость циклом for…of;",
           "- функция printList выводит нумерованный список методом forEach;",
           "- минимальная цена вводится через модуль readline."),
         L("Исходный код программы main.js", "js", r'''
            const readline = require("readline");

            const products = [
                { name: "Молоко", price: 2.35, count: 2 },
                { name: "Хлеб", price: 1.89, count: 1 },
                { name: "Сыр", price: 12.5, count: 1 },
                { name: "Яблоки", price: 3.2, count: 3 },
                { name: "Кофе", price: 15.9, count: 1 }
            ];

            function getTotal(list) {
                let total = 0;
                for (const item of list) {
                    total += item.price * item.count;
                }
                return total;
            }

            function printList(list) {
                list.forEach(function (item, i) {
                    console.log((i + 1) + ". " + item.name + " — " + item.price +
                        " руб. x " + item.count);
                });
            }

            const rl = readline.createInterface({
                input: process.stdin,
                output: process.stdout
            });

            console.log("Список покупок:");
            printList(products);
            console.log("Итого: " + getTotal(products).toFixed(2) + " руб.");

            rl.question("Введите минимальную цену: ", function (answer) {
                const limit = Number(answer);
                const expensive = products.filter(function (item) {
                    return item.price >= limit;
                });
                expensive.sort(function (a, b) {
                    return b.price - a.price;
                });
                console.log("Покупки от " + limit + " руб.:");
                printList(expensive);
                rl.close();
            });
            '''),
         F("Список покупок и общая сумма", "Запусти node main.js; в кадре список покупок и строка «Итого»")],
        [P("Метод filter отбирает покупки с ценой не ниже введённой, метод sort упорядочивает их по убыванию цены."),
         F("Покупки не дешевле 10 рублей", "Запусти node main.js и введи минимальную цену 10; в кадре кофе и сыр")]])

# --- Сайт: HTML/CSS/JS ---
lab(id="web_page", lang="web", kind="web", subject="psip", number="9", topic="Обработка событий на JavaScript",
    goal="Научиться обрабатывать события элементов страницы и изменять содержимое страницы из JavaScript",
    tasks=["Сверстать страницу «Конвертер валют» с полем суммы, списком валют и кнопкой «Перевести»",
           "Написать обработчик нажатия кнопки, который выводит результат перевода на страницу"],
    sections=[
        [P("Страница index.html содержит блок converter с полем amount, списком currency и кнопкой convert, курс "
           "валюты записан в атрибуте value пункта списка. Стили вынесены в файл style.css."),
         L("Исходный код страницы index.html", "html", r'''
            <!DOCTYPE html>
            <html lang="ru">
            <head>
                <meta charset="UTF-8">
                <title>Конвертер валют</title>
                <link rel="stylesheet" href="style.css">
            </head>
            <body>
                <div class="converter">
                    <h1>Конвертер валют</h1>
                    <label for="amount">Сумма в белорусских рублях:</label>
                    <input type="number" id="amount" value="100">
                    <label for="currency">Валюта:</label>
                    <select id="currency">
                        <option value="3.27">Доллар США</option>
                        <option value="3.56">Евро</option>
                        <option value="0.035">Российский рубль</option>
                    </select>
                    <button id="convert">Перевести</button>
                    <p id="result"></p>
                </div>
                <script src="script.js"></script>
            </body>
            </html>
            '''),
         L("Исходный код файла стилей style.css", "css", r'''
            body {
                font-family: Arial, sans-serif;
                background-color: #eef2f5;
                margin: 0;
                padding: 40px;
            }

            .converter {
                width: 340px;
                margin: 0 auto;
                padding: 20px 25px;
                background-color: #ffffff;
                border: 1px solid #c9d3dc;
            }

            label {
                display: block;
                margin-top: 10px;
            }

            input,
            select {
                width: 100%;
                padding: 5px;
                box-sizing: border-box;
            }

            button {
                margin-top: 15px;
                padding: 7px 16px;
                background-color: #2f6f9f;
                color: #ffffff;
                border: none;
            }
            '''),
         F("Страница «Конвертер валют» в браузере", "Открой index.html в браузере; в кадре форма конвертера с полем "
                                                    "суммы, списком валют и кнопкой «Перевести»")],
        [P("Обработчик события click кнопки convert читает сумму и курс выбранной валюты, делит сумму на курс и "
           "выводит результат в абзац result с двумя знаками после запятой."),
         L("Исходный код сценария script.js", "js", r'''
            const button = document.getElementById("convert");
            const result = document.getElementById("result");

            button.addEventListener("click", function () {
                const amount = Number(document.getElementById("amount").value);
                const select = document.getElementById("currency");
                const rate = Number(select.value);
                const name = select.options[select.selectedIndex].text;
                if (amount <= 0) {
                    result.textContent = "Введите сумму больше нуля";
                    return;
                }
                result.textContent = amount + " BYN = " + (amount / rate).toFixed(2) +
                    " (" + name + ")";
            });
            '''),
         F("Результат перевода 100 рублей в доллары",
           "Открой index.html, оставь сумму 100, выбери «Доллар США» и нажми «Перевести»; в кадре строка "
           "«100 BYN = 30.58 (Доллар США)»")]])

# --- PHP: консоль ---
lab(id="php_console", lang="php", kind="console", subject="vp", number="2", topic="Массивы и функции в PHP",
    goal="Научиться работать с ассоциативными массивами и пользовательскими функциями PHP",
    tasks=["Создать ассоциативный массив сотрудников и их зарплат, написать функцию вычисления средней зарплаты",
           "Вывести сотрудников с зарплатой не ниже введённой с клавиатуры, отсортировав по убыванию"],
    sections=[
        [P("Сценарий salary.php запускается в командной строке командой php salary.php:"),
         D("- массив employees хранит фамилии и зарплаты;",
           "- функция averageSalary считает среднее через array_sum и count;",
           "- минимальная зарплата читается из STDIN функцией fgets;",
           "- функция arsort сортирует массив по убыванию зарплаты с сохранением ключей."),
         L("Исходный код сценария salary.php", "php", r'''
            <?php
            $employees = [
                "Ковальчук" => 1450,
                "Романова" => 1820,
                "Зайцев" => 1290,
                "Ткаченко" => 2100,
                "Борисенко" => 1675
            ];

            function averageSalary($list)
            {
                return array_sum($list) / count($list);
            }

            echo "Список сотрудников:\n";
            foreach ($employees as $name => $salary) {
                echo $name . " — " . $salary . " руб.\n";
            }

            echo "Средняя зарплата: " . round(averageSalary($employees), 2) . " руб.\n";

            echo "Введите минимальную зарплату: ";
            $limit = (int) trim(fgets(STDIN));
            arsort($employees);
            echo "Сотрудники с зарплатой от " . $limit . " руб.:\n";
            foreach ($employees as $name => $salary) {
                if ($salary >= $limit) {
                    echo $name . " — " . $salary . " руб.\n";
                }
            }
            '''),
         F("Список сотрудников и средняя зарплата", "Выполни php salary.php в командной строке; в кадре список "
                                                    "сотрудников и средняя зарплата")],
        [F("Сотрудники с зарплатой от 1600 рублей", "Выполни php salary.php и введи 1600; в кадре Ткаченко, Романова и "
                                                    "Борисенко по убыванию зарплаты")]])

# --- PHP: сайт с формой (текст написан настоящим писателем отчётов) ---
lab(id="php_web", lang="php", kind="web", subject="vp", number="4", topic="Обработка HTML-форм на PHP",
    goal="Научиться передавать данные из HTML-формы в PHP-скрипт методом POST и обрабатывать их на сервере",
    tasks=["Создать HTML-форму заказа пиццы (имя, телефон, выбор пиццы из списка, количество) и файл стилей",
           "Написать PHP-скрипт order.php, который принимает данные формы методом POST, проверяет заполнение полей и "
           "выводит сообщение с итоговой стоимостью заказа",
           "Запустить сайт на локальном сервере и проверить работу формы"],
    sections=[
        [P("Создал страницу index.html с формой заказа пиццы и подключил к ней файл стилей style.css. Форма отправляет "
           "данные методом POST в order.php, поля name и phone обязательные, в списке pizza три вида с ценами, в поле "
           "count количество по умолчанию 1."),
         L("Исходный код страницы с формой заказа index.html", "html", r'''
            <!DOCTYPE html>
            <html lang="ru">
            <head>
                <meta charset="UTF-8">
                <title>Заказ пиццы</title>
                <link rel="stylesheet" href="style.css">
            </head>
            <body>
                <h1>Заказ пиццы</h1>
                <form action="order.php" method="post">
                    <label for="name">Ваше имя:</label>
                    <input type="text" id="name" name="name">

                    <label for="phone">Телефон:</label>
                    <input type="text" id="phone" name="phone">

                    <label for="pizza">Пицца:</label>
                    <select id="pizza" name="pizza">
                        <option value="Маргарита">Маргарита — 450 руб.</option>
                        <option value="Пепперони">Пепперони — 520 руб.</option>
                        <option value="Четыре сыра">Четыре сыра — 590 руб.</option>
                    </select>

                    <label for="count">Количество:</label>
                    <input type="number" id="count" name="count" value="1" min="1">

                    <button type="submit">Заказать</button>
                </form>
            </body>
            </html>
            '''),
         L("Исходный код файла стилей style.css", "css", r'''
            body {
                font-family: Arial, sans-serif;
                background-color: #f4f1ea;
                color: #2b2b2b;
                margin: 0;
                padding: 30px;
            }

            h1 {
                font-size: 28px;
                color: #a33a1e;
            }

            form {
                width: 320px;
                background-color: #ffffff;
                padding: 20px;
                border: 1px solid #d8d2c4;
            }

            label {
                display: block;
                margin-top: 12px;
                margin-bottom: 4px;
            }

            input,
            select {
                width: 100%;
                padding: 6px;
                border: 1px solid #b8b0a0;
                box-sizing: border-box;
            }

            button {
                margin-top: 18px;
                padding: 8px 18px;
                background-color: #a33a1e;
                color: #ffffff;
                border: none;
                cursor: pointer;
            }
            '''),
         F("Форма заказа пиццы в браузере до отправки",
           "Запусти сервер командой php -S localhost:8000 в папке с файлами лабы, открой в браузере адрес "
           "localhost:8000/index.html; в кадре адресная строка и форма с полями имени, телефона, списка пицц и "
           "количества.")],
        [P("Разработал скрипт order.php, который принимает поля name, phone, pizza и count методом POST, проверяет их "
           "через empty() и считает сумму по цене выбранной пиццы из массива prices."),
         D("- данные приходят в суперглобальном массиве $_POST, метод задан в атрибуте method=\"post\" формы;",
           "- если хотя бы одно поле пустое, скрипт выводит сообщение «Заполните все поля» и ссылку на возврат к "
           "форме;",
           "- цена берётся из массива prices по названию пиццы, сумма равна цене, умноженной на количество."),
         L("Исходный код обработчика формы order.php", "php", r'''
            <?php
            $name = $_POST['name'] ?? '';
            $phone = $_POST['phone'] ?? '';
            $pizza = $_POST['pizza'] ?? '';
            $count = $_POST['count'] ?? 0;

            $prices = [
                'Маргарита' => 450,
                'Пепперони' => 520,
                'Четыре сыра' => 590
            ];

            if (empty($name) || empty($phone) || empty($pizza) || empty($count)) {
                echo '<p>Заполните все поля</p>';
                echo '<p><a href="index.html">Вернуться к форме</a></p>';
            } else {
                $price = $prices[$pizza];
                $sum = $price * $count;
                echo '<p>Спасибо, ' . $name . '! Ваш заказ: ' . $pizza
                    . ' x ' . $count . '. Сумма: ' . $sum . ' руб.</p>';
            }
            ?>
            ''')],
        [P("Запустил встроенный сервер PHP из папки с файлами лабы командой php -S localhost:8000 и открыл в браузере "
           "адрес http://localhost:8000/index.html."),
         P("Проверил работу формы в двух случаях:"),
         D("- оставил поле «Ваше имя» пустым и нажал «Заказать»: скрипт order.php вывел сообщение «Заполните все поля» "
           "и ссылку «Вернуться к форме»;",
           "- заполнил имя и телефон, выбрал «Пепперони» и количество 2: скрипт вывел «Спасибо, Иван! Ваш заказ: "
           "Пепперони x 2. Сумма: 1040 руб.»."),
         F("Результат обработки заказа в браузере после отправки формы",
           "Запусти сервер командой php -S localhost:8000 и открой http://localhost:8000/index.html → заполни имя и "
           "телефон, выбери пиццу и количество → нажми «Заказать»; в кадре должно быть сообщение со суммой заказа и "
           "адресная строка с order.php.")]])

# --- Go: консоль ---
lab(id="go_console", lang="go", kind="console", subject="ssp", number="9", topic="Срезы и функции в Go",
    goal="Научиться работать со срезами, писать функции с несколькими возвращаемыми значениями",
    tasks=["Написать функции поиска минимума и максимума, среднего значения и отбора чётных чисел среза",
           "Ввести числа с клавиатуры и вывести результаты обработки среза"],
    sections=[
        [P("Программа состоит из трёх функций обработки среза целых чисел:"),
         D("- minMax возвращает два значения — минимум и максимум;", "- average возвращает среднее значение;",
           "- filterEven отбирает чётные числа в новый срез функцией append."),
         L("Исходный код программы main.go", "go", '''
            package main

            import (
            \t"fmt"
            )

            func average(numbers []int) float64 {
            \tsum := 0
            \tfor _, n := range numbers {
            \t\tsum += n
            \t}
            \treturn float64(sum) / float64(len(numbers))
            }

            func minMax(numbers []int) (int, int) {
            \tmin, max := numbers[0], numbers[0]
            \tfor _, n := range numbers[1:] {
            \t\tif n < min {
            \t\t\tmin = n
            \t\t}
            \t\tif n > max {
            \t\t\tmax = n
            \t\t}
            \t}
            \treturn min, max
            }

            func filterEven(numbers []int) []int {
            \tvar result []int
            \tfor _, n := range numbers {
            \t\tif n%2 == 0 {
            \t\t\tresult = append(result, n)
            \t\t}
            \t}
            \treturn result
            }

            func main() {
            \tvar count int
            \tfmt.Print("Введите количество чисел: ")
            \tfmt.Scan(&count)

            \tnumbers := make([]int, 0, count)
            \tfor i := 0; i < count; i++ {
            \t\tvar x int
            \t\tfmt.Printf("Введите число %d: ", i+1)
            \t\tfmt.Scan(&x)
            \t\tnumbers = append(numbers, x)
            \t}

            \tmin, max := minMax(numbers)
            \tfmt.Println("Срез:", numbers)
            \tfmt.Println("Минимум:", min, "максимум:", max)
            \tfmt.Printf("Среднее значение: %.2f\\n", average(numbers))
            \tfmt.Println("Чётные числа:", filterEven(numbers))
            }
            ''')],
        [P("Программа запущена командой go run main.go, введено пять чисел: 14, 3, 8, 21, 6."),
         F("Результат работы программы", "Выполни go run main.go, введи 5 чисел: 14, 3, 8, 21, 6; в кадре срез, "
                                         "минимум, максимум, среднее значение 10.40 и чётные числа")]])

# --- MySQL ---
lab(id="mysql_join", lang="mysql", kind="sql", subject="vp", number="3", topic="Связанные таблицы и запросы с JOIN",
    goal="Научиться создавать связанные таблицы с внешними ключами и выбирать данные из нескольких таблиц",
    tasks=["Создать базу данных shop с таблицами categories и products, связанными внешним ключом",
           "Заполнить таблицы данными", "Выполнить запрос с объединением таблиц JOIN"],
    sections=[
        [P("База данных shop создана в кодировке utf8mb4. Таблица products связана с таблицей categories полем "
           "category_id, на котором задан внешний ключ."),
         L("Создание базы данных и таблиц", "sql", r'''
            CREATE DATABASE shop CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
            USE shop;

            CREATE TABLE categories (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(50) NOT NULL
            );

            CREATE TABLE products (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                price DECIMAL(8, 2) NOT NULL,
                category_id INT NOT NULL,
                FOREIGN KEY (category_id) REFERENCES categories (id)
            );
            '''),
         F("Структура таблицы products в phpMyAdmin", "Открой phpMyAdmin, выбери базу shop и таблицу products, вкладка "
                                                       "«Структура»; в кадре поля таблицы и внешний ключ")],
        [L("Заполнение таблиц данными", "sql", r'''
            INSERT INTO categories (name) VALUES
                ('Ноутбуки'),
                ('Смартфоны'),
                ('Наушники');

            INSERT INTO products (name, price, category_id) VALUES
                ('ASUS VivoBook 15', 1899.00, 1),
                ('Lenovo IdeaPad 3', 1650.00, 1),
                ('Xiaomi Redmi Note 13', 749.00, 2),
                ('Samsung Galaxy A35', 1099.00, 2),
                ('JBL Tune 520BT', 159.00, 3);
            ''')],
        [P("Запрос выбирает название товара, цену и название категории, объединяя таблицы по полю category_id, и "
           "сортирует результат по цене."),
         L("Запрос с объединением таблиц", "sql", r'''
            SELECT products.name, products.price, categories.name AS category
            FROM products
            INNER JOIN categories ON products.category_id = categories.id
            ORDER BY products.price DESC;
            '''),
         F("Результат запроса с JOIN", "Выполни запрос в консоли MySQL; в кадре таблица с товарами, ценами и "
                                       "категориями")]])


# ---------- состояние лабы ----------

def _block(b: dict):
    t = b["type"]
    if t == "paragraph":
        return Paragraph(text=b["text"])
    if t == "dash_list":
        return DashList(items=b["items"])
    if t == "listing":
        return Listing(topic=b["topic"], code=b["code"], language=b["language"])
    return Figure(topic=b["topic"], slot=b["slot"])


def lab_state(lb: dict) -> LabState:
    spec = LabSpec(subject_id=lb["subject"], number=lb["number"], date="10.11.2026", topic=lb["topic"], goal=lb["goal"],
                   tasks=[Task(n=i, text=t) for i, t in enumerate(lb["tasks"], 1)])
    secs = [TaskSection(n=i, title=t, blocks=[_block(b) for b in blocks])
            for i, (t, blocks) in enumerate(zip(lb["tasks"], lb["sections"], strict=True), 1)]
    return LabState(profile_id="eval", subject_id=lb["subject"], number=lb["number"], status="confirmed", spec=spec,
                    report=ReportDoc(lab_number=lb["number"], sections=secs),
                    materials=[Material(name=n, text=t) for n, t in lb.get("materials", ())])


def report_blocks(st: LabState):
    for sec in st.report.sections:
        for bi, b in enumerate(sec.blocks, start=1):
            yield sec.n, bi, b


# ---------- запуск в «консоли» ----------

RAW_NEWLINE = {"go", "php", "javascript", "java"}  # в консоли Windows эти программы получают строку с \r\n


def _drain(fd: int, buf: bytearray, quiet: float, limit: float) -> bool:
    """Читать вывод, пока программа не замолчит на quiet секунд. True — программа закрыла терминал (завершилась)."""
    end = time.monotonic() + limit
    last = time.monotonic()
    while time.monotonic() < end:
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                return True
            if not chunk:
                return True
            if len(buf) < 300_000:
                buf.extend(chunk)
            last = time.monotonic()
        elif time.monotonic() - last >= quiet:
            return False
    return False


def run_tty(cmd: list[str], cwd: Path, env: dict, text: str, crlf: bool, cols: int = 120) -> dict:
    """Программа в псевдотерминале: ввод построчно (после того как программа замолчала), эхо ввода на экране, ввод
    не кончается (программа, ждущая ввода, просто ждёт — как в окне консоли)."""
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, cols, 0, 0))
    attrs = termios.tcgetattr(slave)
    attrs[0] &= ~termios.ICRNL
    attrs[1] &= ~termios.ONLCR
    attrs[3] &= ~termios.ECHOCTL  # \r в эхе — не «^M»
    termios.tcsetattr(slave, termios.TCSANOW, attrs)
    try:
        p = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
                             close_fds=True)
    except OSError as e:
        os.close(slave)
        os.close(master)
        return {"code": -1, "screen": f"не запустилась: {e}", "leftover": 0, "timeout": False}
    os.close(slave)
    buf = bytearray()
    closed = _drain(master, buf, 1.5, 20)
    lines = re.findall(r"[^\n]*\n|[^\n]+$", text)
    leftover = 0
    for i, line in enumerate(lines):
        if closed or p.poll() is not None:
            leftover = len(lines) - i  # в Windows эти Enter съест «pause» — и окно консоли закроется
            break
        try:
            os.write(master, line.replace("\n", "\r\n" if crlf else "\n").encode("utf-8"))
        except OSError:
            leftover = len(lines) - i
            break
        closed = _drain(master, buf, 0.5, 15)
    if not closed:
        closed = _drain(master, buf, 1.5, 15)
    try:
        code = p.wait(2 if closed else 0.3)
    except subprocess.TimeoutExpired:
        code = None  # ждёт ввода: в окне консоли так и остаётся
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            pass
    _drain(master, buf, 0.2, 2)
    os.close(master)
    screen = buf.decode("utf-8", "replace").replace("\r", "")
    return {"code": code, "screen": screen, "leftover": leftover, "timeout": code is None}


def shot_input(shot) -> str:
    """Что ForLabs наберёт в консоли: input, затем шаги type/key enter (как WindowsShooter._console)."""
    text = shot.input or ""
    for step in shot.steps:
        if step.action == "type":
            text += step.value
        elif step.action == "key" and step.value.lower() == "enter":
            text += "\n"
    return text


def console_cmd(plan: ProjectPlan, spec: languages.RunSpec) -> tuple[list[str], dict]:
    """Команда и окружение как в русской Windows: C# — культура ru-RU, Java — локаль ru_RU."""
    cmd, env = list(spec.cmd), {**os.environ, **(spec.env or {})}
    if plan.language == "csharp":
        env.update(LANG="ru_RU.UTF-8", LC_ALL="ru_RU.UTF-8")
    if plan.language == "java":
        cmd[1:1] = ["-Duser.language=ru", "-Duser.country=RU"]
        env.pop("JAVA_TOOL_OPTIONS", None)  # настройки прокси этой машины: «Picked up …» на экране
    if plan.language == "mysql":
        env.pop("MYSQL_PWD", None)
    return cmd, env


# ---------- MySQL ----------

def mysql_reset(plan: ProjectPlan) -> None:
    """Чистый сервер: базы, которые создаёт проект, удаляются (как у студента, который запускает впервые)."""
    names = set()
    for f in plan.files:
        names |= {m[1] for m in safety.SQL_CREATE_DB.finditer(f.content)}
    for sh in plan.shots:
        names |= {m[1] for m in safety.SQL_CREATE_DB.finditer(shot_input(sh))}
    for n in names:
        if n.lower() not in safety.SQL_SYSTEM_DBS:
            subprocess.run(["mysql", "--user=root", "-e", f"DROP DATABASE IF EXISTS `{n}`"], capture_output=True)


def mysql_server() -> bool:
    if not shutil.which("mysql"):
        return False
    r = subprocess.run(["mysql", "--user=root", "-e", "SELECT 1"], capture_output=True)
    return r.returncode == 0


# ---------- окна tkinter и Swing в Xvfb ----------

TK_PROBE = r'''
import json, runpy, sys, tkinter
OUT, MAIN = sys.argv[1], sys.argv[2]
_init = tkinter.Tk.__init__

def collect(tk, path, acc):
    # по Tcl, а не по объектам Python: окно messagebox создаёт сам Tk, у tkinter его объектов нет
    for ch in tk.splitlist(tk.call("winfo", "children", path)):
        ch = str(ch)
        try:
            cls = str(tk.call("winfo", "class", ch))
            if cls in ("Entry", "TEntry", "Spinbox", "TSpinbox", "TCombobox"):
                acc.append(str(tk.call(ch, "get")))
            elif cls == "Text":
                acc.append(str(tk.call(ch, "get", "1.0", "end")))
            elif cls == "Listbox":
                acc.extend(str(x) for x in tk.splitlist(tk.call(ch, "get", 0, "end")))
            elif cls == "Toplevel":
                acc.append(str(tk.call("wm", "title", ch)))
            else:
                var = ""
                try:
                    var = str(tk.call(ch, "cget", "-textvariable"))
                except Exception:
                    pass
                acc.append(str(tk.call("set", "::" + var.lstrip(":"))) if var else str(tk.call(ch, "cget", "-text")))
        except Exception:
            pass
        collect(tk, ch, acc)

def dump(root):
    try:
        acc = [str(root.tk.call("wm", "title", "."))]
        collect(root.tk, ".", acc)
        with open(OUT, "w", encoding="utf-8") as fh:
            json.dump({"texts": acc, "focus": str(root.tk.call("focus"))}, fh, ensure_ascii=False)
    except Exception:
        pass
    root.after(200, dump, root)

def init(self, *a, **k):
    _init(self, *a, **k)
    self.after(200, dump, self)

tkinter.Tk.__init__ = init
sys.argv = [MAIN]
sys.path.insert(0, ".")
runpy.run_path(MAIN, run_name="__main__")
'''

SWING_PROBE = r'''
import java.awt.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import javax.swing.*;
import javax.swing.text.JTextComponent;

public class ForlabsProbe {
    static void collect(Component c, java.util.List<String> out) {
        if (c instanceof JLabel) out.add(((JLabel) c).getText());
        else if (c instanceof AbstractButton) out.add(((AbstractButton) c).getText());
        else if (c instanceof JTextComponent) out.add(((JTextComponent) c).getText());
        else if (c instanceof JList) {
            ListModel<?> m = ((JList<?>) c).getModel();
            for (int i = 0; i < m.getSize(); i++) out.add(String.valueOf(m.getElementAt(i)));
        } else if (c instanceof JComboBox) out.add(String.valueOf(((JComboBox<?>) c).getSelectedItem()));
        else if (c instanceof JTable) {
            JTable t = (JTable) c;
            for (int r = 0; r < t.getRowCount(); r++)
                for (int k = 0; k < t.getColumnCount(); k++) out.add(String.valueOf(t.getValueAt(r, k)));
        }
        if (c instanceof Container) for (Component ch : ((Container) c).getComponents()) collect(ch, out);
    }

    public static void main(String[] args) throws Exception {
        final String out = args[0];
        Thread t = new Thread(() -> {
            while (true) {
                try {
                    Thread.sleep(200);
                    java.util.List<String> texts = new ArrayList<>();
                    for (Window w : Window.getWindows()) {
                        if (!w.isShowing()) continue;
                        if (w instanceof Frame) texts.add(((Frame) w).getTitle());
                        if (w instanceof Dialog) texts.add(((Dialog) w).getTitle());
                        collect(w, texts);
                    }
                    Component f = KeyboardFocusManager.getCurrentKeyboardFocusManager().getFocusOwner();
                    StringBuilder sb = new StringBuilder();
                    sb.append(f == null ? "" : f.getClass().getSimpleName()).append('\n');
                    for (String s : texts) sb.append(s == null ? "" : s.replace("\n", " ")).append('\n');
                    try (Writer wr = new OutputStreamWriter(new FileOutputStream(out), StandardCharsets.UTF_8)) {
                        wr.write(sb.toString());
                    }
                } catch (Exception e) {
                }
            }
        });
        t.setDaemon(true);
        t.start();
        Class.forName(args[1]).getMethod("main", String[].class).invoke(null, (Object) new String[0]);
    }
}
'''

XKEYS = {"tab": "Tab", "enter": "Return", "esc": "Escape", "space": "space", "backspace": "BackSpace",
         "down": "Down", "up": "Up", "left": "Left", "right": "Right", "home": "Home", "end": "End", "f5": "F5"}


class Xvfb:
    """Один экран на все оконные проверки (шаги идут по очереди)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.proc = None
        self.display = ":87"

    def ok(self) -> bool:
        return bool(shutil.which("Xvfb") and shutil.which("xdotool"))

    def start(self) -> None:
        if self.proc is None:
            self.proc = subprocess.Popen(["Xvfb", self.display, "-screen", "0", "1280x800x24", "-nolisten", "tcp"],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1.5)

    def stop(self) -> None:
        if self.proc is not None:
            self.proc.kill()
            self.proc = None


XVFB = Xvfb()


def _xdo(*args: str) -> str:
    env = {**os.environ, "DISPLAY": XVFB.display}
    r = subprocess.run(["xdotool", *args], capture_output=True, text=True, env=env, timeout=30)
    return r.stdout.strip()


def gui_run(cmd: list[str], cwd: Path, shot, dump: Path, reader) -> dict:
    """Окно программы в Xvfb: окно — вперёд (как foreground в Windows), шаги type/key, затем текст окна из зонда."""
    env = {**os.environ, "DISPLAY": XVFB.display, "PYTHONUTF8": "1"}
    env.pop("JAVA_TOOL_OPTIONS", None)
    dump.unlink(missing_ok=True)
    p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         start_new_session=True)
    log = ""
    try:
        wid = ""
        end = time.monotonic() + 20
        while time.monotonic() < end and not wid and p.poll() is None:
            time.sleep(0.3)
            ids = _xdo("search", "--onlyvisible", "--name", ".").split()
            wid = ids[-1] if ids else ""
        if not wid:
            return {"ok": False, "why": "окно не появилось", "log": _out(p)}
        time.sleep(1.0)
        _xdo("windowfocus", "--sync", wid)
        time.sleep(0.5)
        start = reader(dump)
        skipped = []
        for step in shot.steps:
            if step.action == "type":
                _xdo("type", "--delay", "80", "--", step.value)
            elif step.action == "key":
                key = XKEYS.get(step.value.lower())
                if key:
                    _xdo("key", key)
                else:
                    skipped.append(step.value)
            elif step.action == "wait":
                time.sleep(min(float(step.value or 1), 10))
            else:
                skipped.append(step.action)
            time.sleep(0.3)
        time.sleep(1.5)
        texts = reader(dump)
        seen = " | ".join(texts.get("texts", []))
        ok = bool(shot.expect) and shot.expect in seen and not skipped
        why = ("нет expect" if not shot.expect else "ожидаемого текста нет в окне" if shot.expect not in seen
               else "шаги не с клавиатуры (в Swing/tkinter UI Automation не работает)")
        return {"ok": ok, "why": "" if ok else why,
                "start_focus": start.get("focus", ""), "seen": seen[:600], "uia_steps": skipped}
    finally:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        log = _out(p)
        if log.strip():
            pass


def _out(p: subprocess.Popen) -> str:
    try:
        out, _ = p.communicate(timeout=3)
        return (out or b"").decode("utf-8", "replace")[-1500:]
    except Exception:
        return ""


def tk_reader(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def swing_reader(path: Path) -> dict:
    try:
        lines = path.read_text(encoding="utf-8").split("\n")
    except OSError:
        return {}
    return {"focus": lines[0], "texts": [x for x in lines[1:] if x]}


def tk_python() -> str | None:
    for p in ("/usr/bin/python3.12", "/usr/bin/python3", shutil.which("python3") or ""):
        if p and subprocess.run([p, "-c", "import tkinter"], capture_output=True).returncode == 0:
            return p
    return None


def check_gui_live(plan: ProjectPlan, work: Path, tools, artifact) -> list[dict]:
    out = []
    shots = [s for s in plan.shots if s.mode == "window"]
    if not shots or not XVFB.ok():
        return out
    if plan.language == "python":
        py = tk_python()
        if not py:
            return out
        (work / "_probe.py").write_text(TK_PROBE, encoding="utf-8")
        cmd = lambda dump: [py, "_probe.py", str(dump), plan.entry]  # noqa: E731
        reader = tk_reader
    elif plan.language == "java":
        pdir = work / "_probe"
        pdir.mkdir(exist_ok=True)
        (pdir / "ForlabsProbe.java").write_text(SWING_PROBE, encoding="utf-8")
        r = subprocess.run([tools["java"].path, "-d", str(pdir), str(pdir / "ForlabsProbe.java")], capture_output=True)
        if r.returncode != 0:
            return [{"ok": False, "why": "зонд Swing не собрался: " + r.stderr.decode()[-300:]}]
        java = str(Path(tools["java"].path).with_name("java"))
        main = languages._java_main(plan)
        cmd = lambda dump: [java, "-Duser.language=ru", "-Duser.country=RU", *languages.JAVA_RUN,  # noqa: E731
                            "-cp", f"{artifact}:{pdir}", "ForlabsProbe", str(dump), main]
        reader = swing_reader
    else:
        return out
    with XVFB.lock:
        XVFB.start()
        for s in shots:
            dump = work / "_probe_dump.txt"
            res = gui_run(cmd(dump), work, s, dump, reader)
            res["shot"] = f"{s.section}.{s.block}"
            out.append(res)
    return out


# ---------- проверки по коду ----------

def _names_winforms(code: str) -> tuple[set[str], set[str], dict[str, bool]]:
    names = set(re.findall(r'\bName\s*=\s*"([^"]+)"', code))
    texts = set(re.findall(r'\bText\s*=\s*"([^"]+)"', code))
    wired = {}
    for n in names:
        wired[n] = bool(re.search(rf"\b{re.escape(n)}\s*\.\s*Click\s*\+=", code))
    return names, texts, wired


def _names_wpf(xaml: str) -> tuple[set[str], set[str], dict[str, bool]]:
    names = set(re.findall(r'\b(?:x:)?Name\s*=\s*"([^"]+)"', xaml))
    texts = set(re.findall(r'\bContent\s*=\s*"([^"]+)"', xaml))
    wired = {}
    for m in re.finditer(r"<(\w+)\b([^>]*)>", xaml, re.S):
        attrs = m[2]
        n = re.search(r'\b(?:x:)?Name\s*=\s*"([^"]+)"', attrs)
        if n:
            wired[n[1]] = "Click=" in attrs.replace(" ", "")
    return names, texts, wired


def check_gui_static(plan: ProjectPlan) -> list[dict]:
    """WinForms/WPF: элементы из шагов есть (Name / x:Name, у кнопки можно по надписи), нажатия подключены."""
    if plan.kind not in ("winforms", "wpf"):
        return []
    cs = "\n".join(f.content for f in plan.files if f.path.endswith(".cs"))
    xaml = "\n".join(f.content for f in plan.files if f.path.endswith(".xaml"))
    if plan.kind == "winforms":
        names, texts, wired = _names_winforms(cs)
    else:
        names, texts, wired = _names_wpf(xaml)
        wired = {n: w or bool(re.search(rf"\b{re.escape(n)}\s*\.\s*Click\s*\+=", cs)) for n, w in wired.items()}
    out = []
    for s in plan.shots:
        if s.mode != "window":
            continue
        problems = []
        for st in s.steps:
            if st.action in ("set", "select", "check", "click"):
                t = st.target.removeprefix("id:")
                if t in names:
                    if st.action == "click" and not wired.get(t, True):
                        problems.append(f"у «{t}» не подключён Click")
                elif not (st.action == "click" and t in texts):
                    problems.append(f"нет элемента «{t}»")
                if st.action == "select" and f'"{st.value}"' not in cs + xaml and f">{st.value}<" not in xaml:
                    problems.append(f"нет пункта «{st.value}»")
            elif st.action in ("type", "key"):
                problems.append(f"шаг {st.action} в {plan.kind} (лучше set/click по имени)")
        out.append({"shot": f"{s.section}.{s.block}", "ok": not problems and bool(s.expect), "problems": problems,
                    "expect": s.expect})
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def check_listings(st: LabState, plan: ProjectPlan) -> list[dict]:
    refs = {(r.section, r.block): r for r in plan.listings}
    files = {f.path: f.content for f in plan.files}
    out = []
    for n, bi, b in report_blocks(st):
        if not isinstance(b, Listing):
            continue
        r = refs.get((n, bi))
        rec = {"listing": f"{n}.{bi}", "file": r.file if r else None, "override": bool(r and r.code.strip())}
        if r is None:
            where = [p for p, body in files.items() if _norm(b.code) in _norm(body)]
            rec.update(ok=bool(where), unmapped=True, file=where[0] if where else None,
                       why="листинг не привязан к файлу" + ("" if where else ", и его текста нет ни в одном файле"))
        else:
            final = r.code if r.code.strip() else b.code
            body = files.get(r.file, "")
            rec["original_kept"] = _norm(b.code) in _norm(body)
            sync = getattr(proj, "listing_in_file", None)
            got = sync(final, body) if sync is not None else None
            if got and (_norm(final) not in _norm(body) or
                        (_norm(got) != _norm(final) and _norm(got).endswith(_norm(final)))):
                rec["synced"] = f"{len(final)}→{len(got)}"  # этап сам взял листинг из файла (как build_and_shoot)
                final = got
            rec["ok"] = _norm(final) in _norm(body)
            if not rec["ok"]:
                rec["why"] = "текста листинга нет в файле" if r.file in files else "файла нет"
            if rec["override"]:
                rec["override_size"] = f"{len(b.code)}→{len(r.code)}"
        out.append(rec)
    return out


CODE_EXT_ALL = {".cs", ".cpp", ".h", ".hpp", ".py", ".java", ".js", ".php", ".go", ".sql", ".html", ".xaml", ".css"}
# строки, которые видит студент: вывод, заголовки, надписи, сообщения
UI_LINE = re.compile(r"\b(?:Text|Title|Content|Header|ToolTip)\s*=|MessageBox|Console\s*\.\s*Write|\bprint|"
                     r"\binput\s*\(|\btitle\s*\(|\btext\s*=|messagebox|System\s*\.\s*out|setTitle|setText|"
                     r"new\s+J(?:Label|Button|CheckBox|Frame)|JOptionPane|console\s*\.\s*log|textContent|innerHTML|"
                     r"innerText|\balert\s*\(|"
                     r"\becho\b|fmt\s*\.\s*(?:Print|Sprint)|\bcout\b|\bprintf\b|\bputs\b|placeholder")
QUOTED = re.compile(r'"((?:[^"\\\n]|\\.)*)"|\'((?:[^\'\\\n]|\\.)*)\'')


def _english(text: str) -> bool:
    """Надпись по-английски: латинские слова (не коды вроде BYN, OK) и ни одной русской буквы."""
    if re.search(r"[А-Яа-яЁё]", text):
        return False
    words = [w for w in re.findall(r"[A-Za-z]{3,}", text) if not (w.isupper() and len(w) <= 4)]
    return bool(words) and not re.fullmatch(r"[\w./\\-]+\.\w{1,4}", text.strip())


def _comments(text: str, ext: str) -> list[str]:
    if ext in (".py", ".sql"):
        pat = r"#[^\n]*" if ext == ".py" else r"--[^\n]*|/\*.*?\*/"
    elif ext in (".html", ".xaml"):
        pat = r"<!--.*?-->"
    elif ext == ".php":
        pat = r"(?<![:\w])//[^\n]*|/\*.*?\*/|(?<![&\w])#[^\n]*"
    else:
        pat = r"(?<![:\w])//[^\n]*|/\*.*?\*/"
    return re.findall(pat, text, re.S)


def find_artifacts(plan: ProjectPlan) -> list[str]:
    """Следы ИИ: служебные пометки, английские надписи и комментарии, отладочный вывод, рамки, эмодзи, заголовки."""
    out = []
    for f in plan.files:
        ext = PurePosixPath(f.path).suffix.lower()
        if ext not in CODE_EXT_ALL:
            continue
        t = f.content
        for pat, what in [(r"\bTODO\b|\bFIXME\b|\bXXX\b", "TODO/FIXME"),
                          (r"(?i)auto-?generated|\bgenerated\b", "generated"),
                          (r"(?i)lorem ipsum|\bfoo\b|\bJohn Doe\b", "заглушка"),
                          (r"(?i)\bdebug\b|отладк", "debug"), (r"={5,}|-{5,}|\*{5,}|#{4,}", "рамка"),
                          (r"[✅❌✔✖▶•\U0001F300-\U0001FAFF]", "эмодзи/значок"),
                          (r"(?i)press any key|нажмите любую клавишу", "служебный вывод")]:
            for m in re.finditer(pat, t):
                if what == "рамка" and ext in (".sql", ".css"):
                    continue
                out.append(f"{f.path}: {what} «{m[0]}»")
        for c in _comments(t, ext):
            if len(re.findall(r"[A-Za-z]{3,}", c)) >= 2 and not re.search(r"[А-Яа-яЁё]", c):
                out.append(f"{f.path}: английский комментарий «{c.strip()[:60]}»")
        visible = []
        for line in t.split("\n") if ext not in (".html", ".xaml") else []:
            if UI_LINE.search(line):
                visible += [a or b for a, b in QUOTED.findall(line)]
        if ext in (".html", ".xaml", ".php"):
            markup = re.sub(r"<\?php.*?(?:\?>|\Z)|<script\b.*?</script>|<style\b.*?</style>", "", t, flags=re.S | re.I)
            visible += re.findall(r">([^<>]+)<", markup)
            visible += re.findall(r'\b(?:Title|Content|Text|Header|placeholder)="([^"]+)"', markup)
            visible += re.findall(r'<input[^>]*type="(?:submit|button)"[^>]*value="([^"]+)"', markup)
        for v in visible:
            v = v.strip()
            if v and _english(v):
                out.append(f"{f.path}: английская надпись «{v[:50]}»")
        for m in re.finditer(r'\b(?:Title|Text)\s*=\s*"(Form\d*|MainWindow|Window\d*|WindowsFormsApp\d*)"', t):
            out.append(f"{f.path}: заголовок окна «{m[1]}»")
    return sorted(set(out))


# ---------- сайт ----------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def page_url(base: str, shot_url: str) -> str:
    """Адрес страницы снимка так, как его откроет ForLabs (функция этапа «Проект», если она есть)."""
    fn = getattr(proj, "shot_url", None)
    if fn is not None:
        return fn(base, shot_url)
    if shot_url.startswith(("http://127.0.0.1", "http://localhost")):
        return shot_url
    return base.rstrip("/") + "/" + shot_url.lstrip("/")  # как WindowsShooter._browser


def check_web(plan: ProjectPlan, work: Path, tools) -> list[dict]:
    out = []
    shots = [s for s in plan.shots if s.mode == "browser"]
    js = [f for f in plan.files if f.path.endswith(".js")]
    inline = [m for f in plan.files if f.path.endswith((".html", ".htm", ".php"))
              for m in re.findall(r"<script\b[^>]*>(.*?)</script>", f.content, re.S | re.I) if m.strip()]
    if "node" in tools:
        bad = []
        for f in js:
            r = subprocess.run([tools["node"].path, "--check", str(work / f.path)], capture_output=True, text=True)
            if r.returncode:
                bad.append(f"{f.path}: {r.stderr.strip()[-200:]}")
        for i, code in enumerate(inline):
            p = work / f"_inline_{i}.js"
            p.write_text(code, encoding="utf-8")
            r = subprocess.run([tools["node"].path, "--check", str(p)], capture_output=True, text=True)
            p.unlink()
            if r.returncode:
                bad.append(f"встроенный скрипт {i}: {r.stderr.strip()[-200:]}")
        if bad:
            out.append({"shot": "js", "ok": False, "problems": bad})
    if plan.language == "php" and plan.kind == "web" and shots and "php" in tools:
        port = _free_port()
        srv = subprocess.Popen([tools["php"].path, "-S", f"127.0.0.1:{port}", "-t", str(work)], cwd=work,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.0)
        base = f"http://127.0.0.1:{port}/{plan.entry}"
        try:
            for s in shots:
                url = page_url(base, s.url or plan.entry) if s.url else base
                rec = {"shot": f"{s.section}.{s.block}", "url": url.replace(str(port), "PORT")}
                try:
                    with urllib.request.urlopen(url, timeout=10) as r:
                        body = r.read().decode("utf-8", "replace")
                        rec["status"] = r.status
                except urllib.error.HTTPError as e:
                    body, rec["status"] = "", e.code
                except OSError as e:
                    body, rec["status"] = "", str(e)
                errs = re.findall(r"(?:Warning|Fatal error|Notice|Deprecated|Parse error)</b>:[^<]*|"
                                  r"(?:Warning|Fatal error|Notice|Deprecated|Parse error): .*", body)
                css_ok = True
                for href in re.findall(r'href="([^"#?]+\.css)"', body):
                    try:
                        css_url = urllib.parse.urljoin(url, href)
                        with urllib.request.urlopen(css_url, timeout=10) as r:
                            css_ok &= "text/css" in r.headers.get("Content-Type", "")
                    except OSError:
                        css_ok = False
                rec["ok"] = rec.get("status") == 200 and not errs and css_ok and (not s.expect or s.expect in body)
                rec["problems"] = errs[:3] + ([] if css_ok else ["стили не загрузились (адрес страницы)"]) + (
                    [f"нет «{s.expect}»"] if s.expect and s.expect not in body else [])
                out.append(rec)
        finally:
            srv.kill()
    elif shots:
        for s in shots:
            path = (s.url or plan.entry).split("?")[0].split("#")[0].lstrip("/")
            problems = [] if (work / path).is_file() else [f"нет файла {path}"]
            out.append({"shot": f"{s.section}.{s.block}", "ok": not problems, "problems": problems})
    for rec in out:
        s = next((x for x in shots if f"{x.section}.{x.block}" == rec.get("shot")), None)
        if s is not None and s.steps:  # WindowsShooter в браузере только открывает страницу
            rec["ok"] = False
            rec.setdefault("problems", []).append("шаги в браузере не выполняются: снимок будет без них")
    return out


# ---------- один прогон ----------

def make_llm(force_parts: bool = False):
    from forlabs.core.llm import LLM, LLMError
    from forlabs.core.settings import Settings

    llm = LLM(Settings(mode="local", base_url=os.environ["FORLABS_BASE_URL"], model=os.environ["FORLABS_MODEL"]),
              os.environ["FORLABS_API_KEY"])
    if force_parts:  # как будто проект не влез в один ответ (finish_reason=length): дальше — настоящая модель
        real, state = llm.json, {"cut": False}

        def json_cut(messages, schema, *a, **k):
            if schema is ProjectPlan and not state["cut"]:
                state["cut"] = True
                raise LLMError("bad_response", "finish_reason=length", "Ответ модели не поместился в лимит длины")
            return real(messages, schema, *a, **k)

        llm.json = json_cut
    return llm


def run_lab(lb: dict, llm, out_root: Path, tools: dict, reuse: bool, have_mysql: bool) -> dict:
    st = lab_state(lb)
    d = out_root / lb["id"]
    d.mkdir(parents=True, exist_ok=True)
    rec: dict = {"id": lb["id"], "lang": lb["lang"], "kind": lb["kind"]}
    t0 = llm.usage.total_tokens if llm else 0
    started = time.time()
    try:
        saved = next((f for f in (d / "plan.json", d / "plan_model.json") if f.exists()), None)
        if reuse and saved is not None:  # итоговый план (после исправлений), если он есть
            plan = ProjectPlan.model_validate_json(saved.read_text(encoding="utf-8"))
        else:
            plan = proj.plan_project(st, llm)
            rec["plan_requests"] = llm.usage.requests  # больше одного — повтор формата или план по частям
            (d / "plan_model.json").write_text(plan.model_dump_json(indent=1), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        rec.update(error=f"{type(e).__name__}: {getattr(e, 'detail', '') or e}"[:400], valid=False, built=False,
                   tokens=(llm.usage.total_tokens - t0) if llm else 0)
        return rec
    problems = validate(plan)
    rec["valid"] = not problems
    rec["problems"] = problems
    rec["lang_kind"] = f"{plan.language}/{plan.kind}"
    rec["lang_kind_ok"] = plan.language == lb["lang"] and plan.kind == lb["kind"]
    rec["files"] = [f"{f.path} ({len(f.content)})" for f in plan.files]
    if problems:
        rec["built"] = False
        rec["tokens"] = (llm.usage.total_tokens - t0) if llm else 0
        return rec
    plan = proj._with_materials(plan, st)
    work = d / "src"
    shutil.rmtree(work, ignore_errors=True)
    languages.materialize(plan, work, tools)
    gui_cs = plan.language == "csharp" and plan.kind != "console"
    fixes, result = 0, None
    if gui_cs or languages.missing(plan, tools):
        rec["built"] = None  # здесь не собрать (WinForms/WPF — только Windows)
    else:
        result = languages.build(work, plan, tools)
        logs = []
        while not result.ok and llm is not None and not reuse and fixes < proj.MAX_FIXES:
            fixes += 1
            logs.append(result.log[-1500:])
            plan = proj._with_materials(proj.fix_project(plan, result.log, llm), st)
            if validate(plan):
                rec["fix_invalid"] = validate(plan)
                break
            shutil.rmtree(work, ignore_errors=True)
            languages.materialize(plan, work, tools)
            result = languages.build(work, plan, tools)
        rec["built"] = bool(result.ok)
        rec["build_logs"] = logs + ([] if result.ok else [result.log[-1500:]])
    rec["fixes"] = fixes
    (d / "plan.json").write_text(plan.model_dump_json(indent=1), encoding="utf-8")
    rec["safety"] = safety.scan(plan)
    rec["listings"] = check_listings(st, plan)
    rec["artifacts"] = find_artifacts(plan)

    # снимки
    figs = {(n, bi): b for n, bi, b in report_blocks(st) if isinstance(b, Figure)}
    shots = {(s.section, s.block): s for s in plan.shots}
    rec["shots_missing"] = [f"{n}.{bi}" for (n, bi) in figs if (n, bi) not in shots]
    rec["shots_extra"] = [f"{s.section}.{s.block}" for s in plan.shots if (s.section, s.block) not in figs]
    rec["manual"] = [{"shot": f"{s.section}.{s.block}", "reason": s.reason} for s in plan.shots if s.mode == "manual"]
    rec["modes"] = {f"{s.section}.{s.block}": s.mode for s in plan.shots}
    runs = []
    if result is not None and result.ok and not rec["safety"]:
        spec = languages.run_spec(plan, result.artifact, work, tools)
        for s in plan.shots:
            if s.mode != "console":
                continue
            if plan.language == "mysql":
                if not have_mysql:
                    continue
                mysql_reset(plan)
            cmd, env = console_cmd(plan, spec)
            text = shot_input(s)
            r = run_tty(cmd, Path(spec.cwd), env, text, crlf=plan.language in RAW_NEWLINE)
            ok = bool(s.expect) and s.expect in r["screen"]
            entry = {"shot": f"{s.section}.{s.block}", "input": text, "expect": s.expect,
                     "ok": ok and not r["leftover"],
                     "expect_found": ok, "leftover": r["leftover"], "timeout": r["timeout"], "code": r["code"]}
            if not entry["ok"]:
                entry["screen"] = r["screen"][-1200:]
            if plan.language == "mysql":  # второй запуск («Повторить»): база уже есть
                r2 = run_tty(cmd, Path(spec.cwd), env, text, crlf=False)
                entry["rerun_ok"] = bool(s.expect) and s.expect in r2["screen"] and "ERROR" not in r2["screen"]
                entry["errors"] = re.findall(r"ERROR \d+[^\n]*", r["screen"])[:3]
            runs.append(entry)
        if plan.kind == "gui":
            rec["gui_live"] = check_gui_live(plan, work, tools, result.artifact)
    rec["console"] = runs
    rec["gui_static"] = check_gui_static(plan)
    rec["web"] = check_web(plan, work, tools) if plan.kind == "web" or plan.language == "web" else []
    rec["tokens"] = (llm.usage.total_tokens - t0) if llm else 0
    rec["seconds"] = round(time.time() - started)
    return rec


# ---------- учения по исправлению сборки ----------

BREAKS = [  # (язык, что убрать или заменить в коде, на что) — типичные ошибки сборки
    ("java", r"import java\.util\.Scanner;\n", ""),
    ("cpp", r"#include <fstream>\n", ""),
    ("cpp", r"#include <iostream>\n", ""),
    ("go", r'\n\t"fmt"\n', "\n"),
    ("csharp", r"Console\.WriteLine\(", "Console.Writeline("),
    ("python", r'print\("([^"\n]*)"', r'print("\1'),
    ("php", r"(\$employees = \[)", r"\1;"),
    ("javascript", r"(function getTotal\(list\)) \{", r"\1"),
]


def fix_drill(lb: dict, llm, src: Path, out_root: Path, tools: dict) -> dict:
    """Готовый план с внесённой ошибкой сборки → круги исправления настоящей моделью."""
    st = lab_state(lb)
    rec: dict = {"id": lb["id"]}
    plan = ProjectPlan.model_validate_json((src / lb["id"] / "plan.json").read_text(encoding="utf-8"))
    files, broke = list(plan.files), ""
    for lang, pat, rep in BREAKS:
        if lang != plan.language or broke:
            continue
        for i, f in enumerate(files):
            new = re.sub(pat, rep, f.content, count=1)
            if new != f.content:
                files[i], broke = f.model_copy(update={"content": new}), f"{f.path}: {pat}"
                break
    if not broke:
        return {**rec, "skipped": "нечего ломать"}
    rec["broke"] = broke
    plan = plan.model_copy(update={"files": files})
    work = out_root / lb["id"] / "drill"
    shutil.rmtree(work, ignore_errors=True)
    languages.materialize(plan, work, tools)
    result = languages.build(work, plan, tools)
    rec["broken_build_failed"] = not result.ok
    t0, fixes = llm.usage.total_tokens, 0
    while not result.ok and fixes < proj.MAX_FIXES:
        fixes += 1
        plan = proj._with_materials(proj.fix_project(plan, result.log, llm), st)
        if (bad := validate(plan)):
            rec["invalid"] = bad
            break
        shutil.rmtree(work, ignore_errors=True)
        languages.materialize(plan, work, tools)
        result = languages.build(work, plan, tools)
    rec.update(built=bool(result.ok), fixes=fixes, tokens=llm.usage.total_tokens - t0,
               listings=check_listings(st, plan), artifacts=find_artifacts(plan), safety=safety.scan(plan))
    if not result.ok:
        rec["log"] = result.log[-800:]
    return rec


def summary_row(r: dict) -> str:
    def yn(v):
        return "—" if v is None else ("да" if v else "НЕТ")

    con = r.get("console") or []
    gui = (r.get("gui_live") or []) + (r.get("gui_static") or []) + [w for w in r.get("web") or [] if "ok" in w]
    lst = r.get("listings") or []
    checks = con + gui
    return (f"{r['id']:<16} valid={yn(r.get('valid'))} built={yn(r.get('built'))} fixes={r.get('fixes', 0)} "
            f"expect={sum(1 for c in checks if c.get('ok'))}/{len(checks)} "
            f"listings={sum(1 for x in lst if x.get('ok'))}/{len(lst)} manual={len(r.get('manual') or [])} "
            f"missing={len(r.get('shots_missing') or [])} art={len(r.get('artifacts') or [])} "
            f"safety={len(r.get('safety') or [])} tok={r.get('tokens', 0)}"
            + (f" ERR={r['error'][:80]}" if r.get("error") else ""))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True, help="папка для проектов и отчёта (не в репозитории)")
    ap.add_argument("--only", default="", help="id лаб через запятую")
    ap.add_argument("--reuse", action="store_true", help="не звать модель: взять сохранённые планы")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--tag", default="run", help="имя отчёта: report_<tag>.json")
    ap.add_argument("--fix-drill", default="", help="папка прошлого прогона: в его планы вносится ошибка сборки, "
                                                     "исправляет настоящая модель")
    ap.add_argument("--force-parts", action="store_true",
                    help="первый ответ на план «обрезан по длине»: проверка пути «план, потом файлы по одному»")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    labs = [lb for lb in LABS if not args.only or lb["id"] in args.only.split(",")]
    tools = languages.detect()
    have_mysql = mysql_server()
    print("инструменты:", ", ".join(sorted(tools)), "| сервер MySQL:", "да" if have_mysql else "нет", flush=True)
    results: dict[str, dict] = {}
    if args.fix_drill:
        drill = [lb for lb in labs if (Path(args.fix_drill) / lb["id"] / "plan.json").exists()]
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futs = {pool.submit(fix_drill, lb, make_llm(), Path(args.fix_drill), out, tools): lb["id"] for lb in drill}
            for f in futs:
                r = f.result()
                results[futs[f]] = r
                lst = r.get("listings") or []
                print(f"{r['id']:<16} {r.get('skipped') or ''}built={r.get('built')} fixes={r.get('fixes')} "
                      f"listings={sum(1 for x in lst if x.get('ok'))}/{len(lst)} art={len(r.get('artifacts') or [])} "
                      f"tok={r.get('tokens', 0)} {r.get('invalid') or ''}", flush=True)
        total = sum(r.get("tokens", 0) for r in results.values())
        (out / f"report_{args.tag}.json").write_text(json.dumps({"tokens": total, "labs": list(results.values())},
                                                                ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"токенов: {total}")
        return
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futs = {pool.submit(run_lab, lb, None if args.reuse else make_llm(args.force_parts), out, tools,
                                args.reuse, have_mysql): lb["id"] for lb in labs}
            for f in futs:
                lid = futs[f]
                try:
                    results[lid] = f.result()
                except Exception as e:  # noqa: BLE001
                    results[lid] = {"id": lid, "error": f"сбой проверки: {type(e).__name__}: {e}"}
                print(summary_row({"id": lid, **results[lid]}), flush=True)
    finally:
        XVFB.stop()
    ordered = [results[lb["id"]] for lb in labs]
    total = sum(r.get("tokens", 0) for r in ordered)
    (out / f"report_{args.tag}.json").write_text(json.dumps({"tokens": total, "labs": ordered}, ensure_ascii=False,
                                                            indent=1), encoding="utf-8")
    print(f"токенов: {total}; отчёт: {out / f'report_{args.tag}.json'}")


if __name__ == "__main__":
    main()
