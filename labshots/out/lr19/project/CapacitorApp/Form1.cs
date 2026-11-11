using System;
using System.Drawing;
using System.Globalization;
using System.Windows.Forms;
using CapacitorLibrary;

namespace CapacitorApp
{
    public class Form1 : Form
    {
        private TextBox txtS;
        private TextBox txtD;
        private TextBox txtEps;
        private TextBox txtResult;
        private static CultureInfo ru = new CultureInfo("ru-RU");

        public Form1()
        {
            Text = "Ёмкость плоского конденсатора";
            ClientSize = new Size(400, 215);
            Font = new Font("Segoe UI", 10);
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = false;

            txtS = AddRow("Площадь пластины S:", "txtS", 15);
            txtD = AddRow("Расстояние d:", "txtD", 50);
            txtEps = AddRow("Проницаемость eps:", "txtEps", 85);

            Button calc = new Button();
            calc.Name = "btnCalculate";
            calc.Text = "Вычислить";
            calc.Bounds = new Rectangle(15, 125, 180, 32);
            calc.Click += btnCalculate_Click;
            Controls.Add(calc);

            Button clear = new Button();
            clear.Name = "btnClear";
            clear.Text = "Очистить";
            clear.Bounds = new Rectangle(205, 125, 180, 32);
            clear.Click += btnClear_Click;
            Controls.Add(clear);

            txtResult = AddRow("Ёмкость C:", "txtResult", 172);
            txtResult.ReadOnly = true;
        }

        // Подпись и поле ввода в одной строке формы
        private TextBox AddRow(string caption, string name, int y)
        {
            Label l = new Label();
            l.Text = caption;
            l.Location = new Point(15, y + 3);
            l.AutoSize = true;
            Controls.Add(l);

            TextBox t = new TextBox();
            t.Name = name;
            t.Bounds = new Rectangle(185, y, 200, 27);
            Controls.Add(t);
            return t;
        }

        // Число можно ввести и с запятой, и с точкой
        private static bool ReadNumber(TextBox box,
            out double value)
        {
            string text = box.Text.Replace('.', ',');
            return double.TryParse(text, NumberStyles.Float, ru,
                out value);
        }

        private void btnCalculate_Click(object sender,
            EventArgs e)
        {
            double s, d, eps;
            if (!ReadNumber(txtS, out s)
                || !ReadNumber(txtD, out d)
                || !ReadNumber(txtEps, out eps))
            {
                MessageBox.Show("Введите числа S, d и eps!",
                    "Ошибка");
                return;
            }
            try
            {
                // расчёт выполняет метод библиотеки
                double c = Capacitor.Capacity(eps, s, d);
                txtResult.Text = c.ToString("F6", ru);
            }
            catch (ArgumentException ex)
            {
                txtResult.Text = "";
                MessageBox.Show(ex.Message + "!", "Ошибка");
            }
        }

        private void btnClear_Click(object sender, EventArgs e)
        {
            txtS.Text = "";
            txtD.Text = "";
            txtEps.Text = "";
            txtResult.Text = "";
        }
    }
}
