using System;

namespace ClassLibrary1
{
    /// <summary>
    /// Математический класс
    /// </summary>
    public class Calculator
    {
        /// <summary>
        /// Метод возвращает сумму двух целых чисел
        /// </summary>
        public static int Summ(int firstNumber, int secondNumber)
        {
            return firstNumber + secondNumber;
        }

        /// <summary>
        /// Метод возвращает разность двух целых чисел
        /// </summary>
        public static int Division(int firstNumber,
            int secondNumber)
        {
            return firstNumber - secondNumber;
        }

        /// <summary>
        /// Метод возвращает произведение двух чисел
        /// </summary>
        public static long Multiply(long x, long y)
        {
            return (x * y);
        }

        /// <summary>
        /// Метод возвращает деление двух чисел
        /// </summary>
        public static int Residual(int firstNumber,
            int secondNumber)
        {
            return (firstNumber / secondNumber);
        }

        /// <summary>
        /// Сумма двух дробных чисел
        /// </summary>
        public static double Add(double a, double b)
        {
            return a + b;
        }

        /// <summary>
        /// Разность двух дробных чисел
        /// </summary>
        public static double Subtract(double a, double b)
        {
            return a - b;
        }

        /// <summary>
        /// Произведение двух дробных чисел
        /// </summary>
        public static double Mult(double a, double b)
        {
            return a * b;
        }

        /// <summary>
        /// Частное двух дробных чисел
        /// </summary>
        public static double Divide(double a, double b)
        {
            if (b == 0)
            {
                throw new DivideByZeroException(
                    "Деление на ноль");
            }
            return a / b;
        }
    }
}
