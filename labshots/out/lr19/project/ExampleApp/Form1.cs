using System;
using System.Drawing;
using System.Windows.Forms;
using ClassLibrary1;

namespace ExampleApp
{
    public class Form1 : Form
    {
        private TextBox txtFirst;
        private TextBox txtSecond;
        private Label lblResult;

        public Form1()
        {
            Text = "Проверка библиотеки ClassLibrary1";
            ClientSize = new Size(380, 170);
            Font = new Font("Segoe UI", 10);
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = false;

            Controls.Add(MakeLabel("Первое число:", 15, 18));
            txtFirst = MakeBox("txtFirst", 150, 15);
            Controls.Add(MakeLabel("Второе число:", 15, 53));
            txtSecond = MakeBox("txtSecond", 150, 50);

            string[] ops = { "+", "-", "*", "/" };
            for (int i = 0; i < ops.Length; i++)
            {
                Button b = new Button();
                b.Name = "btnOp" + i;
                b.Text = ops[i];
                b.Bounds = new Rectangle(15 + i * 90, 88, 80, 32);
                b.Click += Operation_Click;
                Controls.Add(b);
            }

            lblResult = MakeLabel("Результат:", 15, 133);
            lblResult.Name = "lblResult";
            lblResult.AutoSize = true;
        }

        private Label MakeLabel(string text, int x, int y)
        {
            Label l = new Label();
            l.Text = text;
            l.Location = new Point(x, y);
            l.AutoSize = true;
            Controls.Add(l);
            return l;
        }

        private TextBox MakeBox(string name, int x, int y)
        {
            TextBox t = new TextBox();
            t.Name = name;
            t.Bounds = new Rectangle(x, y, 210, 27);
            Controls.Add(t);
            return t;
        }

        private void Operation_Click(object sender, EventArgs e)
        {
            int a, b;
            if (!int.TryParse(txtFirst.Text, out a)
                || !int.TryParse(txtSecond.Text, out b))
            {
                lblResult.Text = "Результат: введите целые числа";
                return;
            }
            string op = ((Button)sender).Text;
            try
            {
                long result = 0;
                if (op == "+") result = Calculator.Summ(a, b);
                if (op == "-") result = Calculator.Division(a, b);
                if (op == "*") result = Calculator.Multiply(a, b);
                if (op == "/") result = Calculator.Residual(a, b);
                lblResult.Text = "Результат: " + a + " " + op
                    + " " + b + " = " + result;
            }
            catch (DivideByZeroException)
            {
                lblResult.Text = "Результат: деление на ноль";
            }
        }
    }
}
