using System;
using System.Drawing;
using System.Globalization;
using System.Windows.Forms;
using ClassLibrary1;

namespace CalculatorApp
{
    public class Form1 : Form
    {
        private TextBox display;
        private Label history;
        private double accumulator = 0;
        private string pending = "";
        private bool newEntry = true;

        public Form1()
        {
            Text = "Калькулятор";
            ClientSize = new Size(300, 400);
            Font = new Font("Segoe UI", 12);
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = false;

            history = new Label();
            history.Name = "history";
            history.Bounds = new Rectangle(10, 8, 280, 22);
            history.TextAlign = ContentAlignment.MiddleRight;
            history.ForeColor = Color.DimGray;
            Controls.Add(history);

            display = new TextBox();
            display.Name = "display";
            display.Bounds = new Rectangle(10, 32, 280, 40);
            display.Font = new Font("Segoe UI", 20);
            display.TextAlign = HorizontalAlignment.Right;
            display.ReadOnly = true;
            display.Text = "0";
            Controls.Add(display);

            string[,] keys =
            {
                { "C", "←", "±", "÷" },
                { "7", "8", "9", "×" },
                { "4", "5", "6", "−" },
                { "1", "2", "3", "+" },
                { "0", ",", "=", "" }
            };
            for (int row = 0; row < 5; row++)
            {
                for (int col = 0; col < 4; col++)
                {
                    if (keys[row, col] != "")
                    {
                        AddKey(keys[row, col], col, row);
                    }
                }
            }
        }

        private void AddKey(string text, int col, int row)
        {
            Button b = new Button();
            b.Text = text;
            b.Name = "key" + row + col;
            int width = text == "=" ? 140 : 68;
            b.Bounds = new Rectangle(10 + col * 72, 84 + row * 62,
                width, 56);
            b.Click += Key_Click;
            Controls.Add(b);
        }

        private void Key_Click(object sender, EventArgs e)
        {
            string key = ((Button)sender).Text;
            if (char.IsDigit(key[0]))
            {
                display.Text = newEntry || display.Text == "0"
                    ? key : display.Text + key;
                newEntry = false;
            }
            else if (key == ",")
            {
                if (newEntry)
                {
                    display.Text = "0";
                    newEntry = false;
                }
                if (!display.Text.Contains(","))
                {
                    display.Text += ",";
                }
            }
            else if (key == "C")
            {
                accumulator = 0;
                pending = "";
                history.Text = "";
                display.Text = "0";
                newEntry = true;
            }
            else if (key == "←")
            {
                string t = display.Text;
                display.Text = t.Length > 1
                    ? t.Substring(0, t.Length - 1) : "0";
            }
            else if (key == "±")
            {
                double v = Value();
                display.Text = Format(-v);
            }
            else if (key == "=")
            {
                if (pending != "")
                {
                    history.Text = Format(accumulator) + " "
                        + pending + " " + display.Text + " =";
                    Calculate();
                    pending = "";
                }
            }
            else
            {
                if (pending != "" && !newEntry)
                {
                    Calculate();
                }
                else
                {
                    accumulator = Value();
                }
                pending = key;
                history.Text = Format(accumulator) + " " + key;
                newEntry = true;
            }
        }

        private static CultureInfo ru = new CultureInfo("ru-RU");

        private double Value()
        {
            return double.Parse(display.Text, ru);
        }

        private string Format(double v)
        {
            return v.ToString("0.##########", ru);
        }

        private void Calculate()
        {
            double b = Value();
            try
            {
                double a = accumulator;
                if (pending == "+") a = Calculator.Add(a, b);
                if (pending == "−") a = Calculator.Subtract(a, b);
                if (pending == "×") a = Calculator.Mult(a, b);
                if (pending == "÷") a = Calculator.Divide(a, b);
                accumulator = a;
                display.Text = Format(accumulator);
            }
            catch (DivideByZeroException)
            {
                display.Text = "Деление на 0";
                accumulator = 0;
                pending = "";
            }
            newEntry = true;
        }
    }
}
